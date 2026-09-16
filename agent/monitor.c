/*
 * imagectl-monitor -- expose /dev/fb0 over standard RFB using LibVNCServer.
 * With --fb FILE (a regular file) it serves the memory framebuffer that
 * native-gui --backend mem draws when the machine has no display (#835).
 *
 * View-only unless --input is supplied. In input mode this process creates
 * its own uinput keyboard/mouse. native-gui's periodic input_rescan() then
 * discovers and EVIOCGRABs that event device.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <rfb/keysym.h>
#include <rfb/rfb.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define DEFAULT_PORT 5900
#define DEFAULT_FPS 10
#define UINPUT_NAME "ImageCtl remote monitor"
#define ARRAY_LEN(a) (sizeof(a) / sizeof((a)[0]))

static volatile sig_atomic_t stopping;
static int input_fd = -1;
static int pointer_x;
static int pointer_y;
static int pointer_known;
static int previous_buttons;
/* Keys this process currently holds down through uinput (#839): released
 * when the client that pressed them goes away mid-press. */
static unsigned char key_down[KEY_MAX + 1];

/*
 * The boot secret (#839). 5900 is bound to the distribution address and
 * every host on that VLAN can open it, so the RFB security handshake is the
 * gate -- for the screen, for --input, and for the power buttons alike. The
 * agent draws 16 random bytes per boot (agent/lib/monitor.sh), reports them
 * to the server in hello, and hands them here as a 32-hex file. Only the
 * server's proxy (server/monitor.py) answers the challenge with them; the
 * browser never sees the secret and keeps its old None handshake.
 *
 * Wire format: RFB security type 2 (VNC Authentication) framing -- 16-byte
 * challenge, 16-byte response -- because that is the one type LibVNCServer
 * offers *instead of* None once authPasswdData is set. The response is NOT
 * DES(challenge); it is the raw 16 secret bytes, and the challenge is
 * unused. Price, stated: a passive sniffer on the distribution VLAN can
 * replay the secret until the machine reboots. That attacker needs ARP
 * spoofing or a mirror port, and with either could also hijack the
 * authenticated TCP session; the answer to him is the network model (#702),
 * not a challenge-response. Upgrading the response to a keyed hash is a
 * one-function change on each side and needs no wire change.
 */
#define SECRET_LEN 16
static unsigned char secret[SECRET_LEN];
static unsigned long auth_failures;

static void stop_handler(int signo) {
    (void)signo;
    stopping = 1;
}

static void fatal(const char *what) {
    fprintf(stderr, "imagectl-monitor: FATAL: %s: %s\n", what, strerror(errno));
    exit(EXIT_FAILURE);
}

static int parse_int(const char *text, int minimum, int maximum,
                     const char *name) {
    char *end = NULL;
    long value;

    errno = 0;
    value = strtol(text, &end, 10);
    if (errno || !end || *end || value < minimum || value > maximum) {
        fprintf(stderr, "imagectl-monitor: invalid %s: %s\n", name, text);
        exit(EXIT_FAILURE);
    }
    return (int)value;
}

/*
 * Runs inside LibVNCServer's client callbacks. A short write or EAGAIN on
 * the non-blocking uinput fd drops this one event and says so -- it must
 * never exit(): a burst of pointer events from the console is not a reason
 * to take the monitor down (#839, was fatal()).
 */
static void emit_event(unsigned short type, unsigned short code, int value) {
    static unsigned long dropped;
    struct input_event event = {
        .type = type,
        .code = code,
        .value = value,
    };

    if (input_fd < 0)
        return;
    if (write(input_fd, &event, sizeof(event)) == (ssize_t)sizeof(event))
        return;
    dropped++;
    if (dropped <= 5 || dropped % 1000 == 0)
        fprintf(stderr, "imagectl-monitor: dropped input event %lu: %s\n",
                dropped, strerror(errno));
}

static void sync_input(void) {
    emit_event(EV_SYN, SYN_REPORT, 0);
}

static void enable_key(int fd, int code) {
    if (ioctl(fd, UI_SET_KEYBIT, code) < 0)
        fatal("UI_SET_KEYBIT");
}

static int create_uinput(void) {
    static const int keys[] = {
        KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I,
        KEY_J, KEY_K, KEY_L, KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R,
        KEY_S, KEY_T, KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y, KEY_Z,
        KEY_0, KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8,
        KEY_9, KEY_ENTER, KEY_BACKSPACE, KEY_TAB, KEY_ESC, KEY_SPACE,
        KEY_LEFTSHIFT, KEY_RIGHTSHIFT, KEY_LEFTCTRL, KEY_RIGHTCTRL,
        KEY_LEFTALT, KEY_RIGHTALT, KEY_LEFTMETA, KEY_RIGHTMETA,
        KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_HOME, KEY_END,
        KEY_PAGEUP, KEY_PAGEDOWN, KEY_INSERT, KEY_DELETE,
        KEY_MINUS, KEY_EQUAL, KEY_LEFTBRACE, KEY_RIGHTBRACE,
        KEY_SEMICOLON, KEY_APOSTROPHE, KEY_GRAVE, KEY_BACKSLASH,
        KEY_COMMA, KEY_DOT, KEY_SLASH,
        KEY_F1, KEY_F2, KEY_F3, KEY_F4, KEY_F5, KEY_F6,
        KEY_F7, KEY_F8, KEY_F9, KEY_F10, KEY_F11, KEY_F12,
        BTN_LEFT
    };
    struct uinput_setup setup = {
        .id = {
            .bustype = BUS_VIRTUAL,
            .vendor = 0x1d6b,
            .product = 0x690,
            .version = 1,
        },
    };
    size_t i;
    int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);

    if (fd < 0)
        fatal("open /dev/uinput");
    if (ioctl(fd, UI_SET_EVBIT, EV_KEY) < 0 ||
        ioctl(fd, UI_SET_EVBIT, EV_REL) < 0 ||
        ioctl(fd, UI_SET_RELBIT, REL_X) < 0 ||
        ioctl(fd, UI_SET_RELBIT, REL_Y) < 0)
        fatal("configure /dev/uinput");

    for (i = 0; i < ARRAY_LEN(keys); i++)
        enable_key(fd, keys[i]);

    snprintf(setup.name, sizeof(setup.name), "%s", UINPUT_NAME);
    if (ioctl(fd, UI_DEV_SETUP, &setup) < 0 ||
        ioctl(fd, UI_DEV_CREATE) < 0)
        fatal("create uinput device");

    fprintf(stderr, "imagectl-monitor: input enabled through %s\n",
            UINPUT_NAME);
    return fd;
}

struct key_pair {
    rfbKeySym symbol;
    int code;
};

static const struct key_pair special_keys[] = {
    {XK_Return, KEY_ENTER}, {XK_BackSpace, KEY_BACKSPACE},
    {XK_Tab, KEY_TAB}, {XK_Escape, KEY_ESC}, {XK_space, KEY_SPACE},
    {XK_Shift_L, KEY_LEFTSHIFT}, {XK_Shift_R, KEY_RIGHTSHIFT},
    {XK_Control_L, KEY_LEFTCTRL}, {XK_Control_R, KEY_RIGHTCTRL},
    {XK_Alt_L, KEY_LEFTALT}, {XK_Alt_R, KEY_RIGHTALT},
    {XK_Meta_L, KEY_LEFTMETA}, {XK_Meta_R, KEY_RIGHTMETA},
    {XK_Super_L, KEY_LEFTMETA}, {XK_Super_R, KEY_RIGHTMETA},
    {XK_Up, KEY_UP}, {XK_Down, KEY_DOWN},
    {XK_Left, KEY_LEFT}, {XK_Right, KEY_RIGHT},
    {XK_Home, KEY_HOME}, {XK_End, KEY_END},
    {XK_Page_Up, KEY_PAGEUP}, {XK_Page_Down, KEY_PAGEDOWN},
    {XK_Insert, KEY_INSERT}, {XK_Delete, KEY_DELETE},
    {XK_F1, KEY_F1}, {XK_F2, KEY_F2}, {XK_F3, KEY_F3},
    {XK_F4, KEY_F4}, {XK_F5, KEY_F5}, {XK_F6, KEY_F6},
    {XK_F7, KEY_F7}, {XK_F8, KEY_F8}, {XK_F9, KEY_F9},
    {XK_F10, KEY_F10}, {XK_F11, KEY_F11}, {XK_F12, KEY_F12},
};

static int ascii_key(rfbKeySym symbol) {
    static const int letters[] = {
        KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I,
        KEY_J, KEY_K, KEY_L, KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R,
        KEY_S, KEY_T, KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y, KEY_Z
    };
    static const int digits[] = {
        KEY_0, KEY_1, KEY_2, KEY_3, KEY_4,
        KEY_5, KEY_6, KEY_7, KEY_8, KEY_9
    };

    if (symbol >= 'a' && symbol <= 'z')
        return letters[symbol - 'a'];
    if (symbol >= 'A' && symbol <= 'Z')
        return letters[symbol - 'A'];
    if (symbol >= '0' && symbol <= '9')
        return digits[symbol - '0'];

    switch (symbol) {
    case '-': case '_': return KEY_MINUS;
    case '=': case '+': return KEY_EQUAL;
    case '[': case '{': return KEY_LEFTBRACE;
    case ']': case '}': return KEY_RIGHTBRACE;
    case ';': case ':': return KEY_SEMICOLON;
    case '\'': case '"': return KEY_APOSTROPHE;
    case '`': case '~': return KEY_GRAVE;
    case '\\': case '|': return KEY_BACKSLASH;
    case ',': case '<': return KEY_COMMA;
    case '.': case '>': return KEY_DOT;
    case '/': case '?': return KEY_SLASH;
    default: return -1;
    }
}

static int keycode_for(rfbKeySym symbol) {
    size_t i;
    int code = ascii_key(symbol);

    if (code >= 0)
        return code;
    for (i = 0; i < ARRAY_LEN(special_keys); i++)
        if (special_keys[i].symbol == symbol)
            return special_keys[i].code;
    return -1;
}

static void keyboard_event(rfbBool down, rfbKeySym symbol,
                           rfbClientPtr client) {
    int code;
    (void)client;

    if (input_fd < 0)
        return;
    code = keycode_for(symbol);
    if (code < 0) {
        if (down)
            fprintf(stderr,
                    "imagectl-monitor: unmapped RFB keysym 0x%lx\n",
                    (unsigned long)symbol);
        return;
    }
    key_down[code] = down ? 1 : 0;
    emit_event(EV_KEY, (unsigned short)code, down ? 1 : 0);
    sync_input();
}

/*
 * A client that disconnects mid-press (browser tab closed while holding
 * Shift, proxy cut during a drag) must not leave the key or the button held
 * in the kernel: the next operator would inherit a stuck modifier (#839).
 */
static void client_gone(rfbClientPtr client) {
    int code, released = 0;

    fprintf(stderr, "imagectl-monitor: client %s gone\n",
            client->host ? client->host : "?");
    if (input_fd < 0)
        return;
    for (code = 0; code <= KEY_MAX; code++) {
        if (!key_down[code])
            continue;
        key_down[code] = 0;
        emit_event(EV_KEY, (unsigned short)code, 0);
        released++;
    }
    if (previous_buttons & 1) {
        emit_event(EV_KEY, BTN_LEFT, 0);
        released++;
    }
    previous_buttons = 0;
    pointer_known = 0;
    if (released)
        sync_input();
}

static enum rfbNewClientAction new_client(rfbClientPtr client) {
    client->clientGoneHook = client_gone;
    return RFB_CLIENT_ACCEPT;
}

/*
 * The passwordCheck of the VNC Authentication framing (see `secret` above):
 * `response` is what the client sent back for the 16-byte challenge, and it
 * must be the secret itself. Constant-time compare; a wrong length is a
 * plain reject. Failures are counted and logged sparsely -- a scanner on
 * the VLAN must not fill the agent log on tmpfs.
 */
static rfbBool secret_check(rfbClientPtr client, const char *response,
                            int len) {
    unsigned char diff = 0;
    int i;

    if (len == SECRET_LEN)
        for (i = 0; i < SECRET_LEN; i++)
            diff |= (unsigned char)response[i] ^ secret[i];
    else
        diff = 1;
    if (!diff)
        return TRUE;
    auth_failures++;
    if (auth_failures <= 10 || auth_failures % 100 == 0)
        fprintf(stderr, "imagectl-monitor: rejected client %s "
                "(bad secret, %lu so far)\n",
                client->host ? client->host : "?", auth_failures);
    return FALSE;
}

static int hex_nibble(int c) {
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    return -1;
}

/* 32 lowercase hex characters, optionally newline-terminated. Anything
 * else is a refusal to start: an unauthenticated 5900 is never the
 * fallback (the agent's supervisor logs the reason and retries). */
static void load_secret(const char *path) {
    char text[SECRET_LEN * 2 + 2];
    ssize_t got;
    int i, fd = open(path, O_RDONLY | O_CLOEXEC);

    if (fd < 0)
        fatal("open secret file");
    got = read(fd, text, sizeof(text));
    close(fd);
    if (got < 0)
        fatal("read secret file");
    if (got == SECRET_LEN * 2 + 1 && text[SECRET_LEN * 2] == '\n')
        got--;
    if (got != SECRET_LEN * 2) {
        fprintf(stderr, "imagectl-monitor: FATAL: %s: expected %d hex "
                "characters, got %lld bytes\n", path, SECRET_LEN * 2,
                (long long)got);
        exit(EXIT_FAILURE);
    }
    for (i = 0; i < SECRET_LEN; i++) {
        int high = hex_nibble(text[2 * i]), low = hex_nibble(text[2 * i + 1]);
        if (high < 0 || low < 0) {
            fprintf(stderr, "imagectl-monitor: FATAL: %s: not lowercase hex\n",
                    path);
            exit(EXIT_FAILURE);
        }
        secret[i] = (unsigned char)(high << 4 | low);
    }
}

/*
 * View-only (#833): LibVNCServer calls kbdAddEvent/ptrAddEvent without a
 * NULL check, so a NULL callback is a jump to address 0 -- SIGSEGV on the
 * first KeyEvent/PointerEvent, which the console client sends right after
 * the handshake. The cloner invariant is "no uinput, no injection", and
 * these no-ops keep it: the events arrive and are dropped, nothing else.
 */
static void keyboard_event_ignored(rfbBool down, rfbKeySym symbol,
                                   rfbClientPtr client) {
    (void)down;
    (void)symbol;
    (void)client;
}

static void pointer_event_ignored(int buttons, int x, int y,
                                  rfbClientPtr client) {
    (void)buttons;
    (void)x;
    (void)y;
    (void)client;
}

static void pointer_event(int buttons, int x, int y, rfbClientPtr client) {
    int dx, dy, left;
    (void)client;

    if (input_fd < 0)
        return;

    if (!pointer_known) {
        pointer_x = x;
        pointer_y = y;
        pointer_known = 1;
    }
    dx = x - pointer_x;
    dy = y - pointer_y;
    pointer_x = x;
    pointer_y = y;

    if (dx)
        emit_event(EV_REL, REL_X, dx);
    if (dy)
        emit_event(EV_REL, REL_Y, dy);

    left = !!(buttons & 1);
    if (left != !!(previous_buttons & 1))
        emit_event(EV_KEY, BTN_LEFT, left);
    previous_buttons = buttons;
    sync_input();
}

/*
 * Power control (#781). The remote monitor toolbar replaced Ctrl+Alt+Del
 * with Reboot/Power-off. The browser sends the request as an RFB
 * ClientCutText carrying an exact token; LibVNCServer hands it here. Power
 * is deliberately independent of --input (view-only cloners must reboot
 * too): it is not a screen keystroke. The gate is the RFB security
 * handshake above (#839) -- LibVNCServer delivers no client message,
 * this one included, before secret_check() has passed, and only the
 * server's proxy holds the secret. The admin-only WebSocket is the gate in
 * front of the proxy; it was never a gate in front of this port.
 * Any other cut text is an ordinary clipboard paste and is ignored.
 */
#define POWER_REBOOT "imagectl-power:reboot"
#define POWER_POWEROFF "imagectl-power:poweroff"

static void run_power(char *const argv[]) {
    pid_t pid;

    sync();
    pid = fork();
    if (pid == 0) {
        execvp(argv[0], argv);
        _exit(127);          /* exec failed -- never return to the RFB loop. */
    }
    /* Parent does not wait: the machine is going down under it. A failed
     * fork leaves the machine up, which is the safe direction. A failed
     * exec is reaped by the kernel -- SIGCHLD is SIG_IGN in main() (#839),
     * so no zombie survives it. */
}

static void text_event(char *str, int len, rfbClientPtr client) {
    (void)client;

    if (len == (int)strlen(POWER_REBOOT) && !memcmp(str, POWER_REBOOT, (size_t)len)) {
        char *argv[] = {(char *)"reboot", (char *)"-f", NULL};
        fprintf(stderr, "imagectl-monitor: remote reboot requested\n");
        run_power(argv);
    } else if (len == (int)strlen(POWER_POWEROFF) &&
               !memcmp(str, POWER_POWEROFF, (size_t)len)) {
        char *argv[] = {(char *)"poweroff", (char *)"-f", NULL};
        fprintf(stderr, "imagectl-monitor: remote poweroff requested\n");
        run_power(argv);
    }
}

/*
 * Memory framebuffer (#835). A machine with no display attached has no
 * /dev/fb0 (i915: "Cannot find any crtc or sizes"), and the monitor exists
 * for exactly that machine. native-gui --backend mem then draws into a
 * regular file, and --fb names it here: a 4096-byte header, then XRGB8888
 * rows of `stride` bytes. `seq` is the GUI's seqlock -- odd while a frame is
 * being copied in -- so a torn frame is skipped, never served. The layout is
 * docs/interfaces.md s15; the constants are repeated verbatim in
 * native-gui/src/backend.c and tests/test_headless_framebuffer.py pins them.
 */
#define MEMFB_MAGIC "IMCTLFB1"
#define MEMFB_HEADER_BYTES 4096
#define MEMFB_FORMAT_XRGB8888 1

struct memfb_header {
    char magic[8];
    uint32_t width, height, stride, format, seq;
};

/* Fills var/fix from the file's header so the fbdev checks and the copy
 * loop below run unchanged. Positive evidence only: magic, format and a
 * size that really holds every row -- a file that merely exists is not a
 * framebuffer (principle 5). */
static void memfb_describe(int fd, off_t size, struct fb_var_screeninfo *var,
                           struct fb_fix_screeninfo *fix) {
    struct memfb_header header;

    if (pread(fd, &header, sizeof(header), 0) != (ssize_t)sizeof(header))
        fatal("read memory framebuffer header");
    if (memcmp(header.magic, MEMFB_MAGIC, sizeof(header.magic)) ||
        header.format != MEMFB_FORMAT_XRGB8888 ||
        header.width == 0 || header.height == 0 ||
        header.stride < header.width * 4 ||
        (uint64_t)size < MEMFB_HEADER_BYTES +
            (uint64_t)header.stride * header.height) {
        fprintf(stderr,
                "imagectl-monitor: FATAL: not a memory framebuffer: "
                "%ux%u, stride %u, format %u, %lld bytes\n",
                header.width, header.height, header.stride, header.format,
                (long long)size);
        exit(EXIT_FAILURE);
    }
    memset(var, 0, sizeof(*var));
    memset(fix, 0, sizeof(*fix));
    var->xres = header.width;
    var->yres = header.height;
    var->bits_per_pixel = 32;
    var->red.length = var->green.length = var->blue.length = 8;
    fix->line_length = header.stride;
    fix->smem_len = (uint32_t)size;
    fprintf(stderr, "imagectl-monitor: serving a memory framebuffer, %ux%u\n",
            header.width, header.height);
}

static void usage(const char *program) {
    fprintf(stderr,
            "usage: %s --bind ADDRESS --secret-file FILE [--port PORT]\n"
            "          [--fps FPS] [--input]\n"
            "          [--fb DEVICE | --fb MEMFB-FILE | --fb FILE --geometry WxH]\n",
            program);
    exit(EXIT_FAILURE);
}

int main(int argc, char **argv) {
    const char *bind_address = NULL;
    const char *secret_path = NULL;
    const char *fb_path = "/dev/fb0";
    const char *geometry = NULL;
    struct fb_var_screeninfo var;
    struct fb_fix_screeninfo fix;
    struct stat fb_stat;
    rfbScreenInfoPtr screen;
    uint8_t *mapped;
    size_t mapped_length;
    size_t pixel_offset = 0;             /* MEMFB_HEADER_BYTES for a file */
    const uint32_t *frame_seq = NULL;    /* the GUI's seqlock, files only */
    char *rfb_framebuffer;
    int port = DEFAULT_PORT;
    int fps = DEFAULT_FPS;
    int input_enabled = 0;
    int fb_fd;
    int i;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--bind") && i + 1 < argc)
            bind_address = argv[++i];
        else if (!strcmp(argv[i], "--port") && i + 1 < argc)
            port = parse_int(argv[++i], 1, 65535, "port");
        else if (!strcmp(argv[i], "--fps") && i + 1 < argc)
            fps = parse_int(argv[++i], 1, 30, "fps");
        else if (!strcmp(argv[i], "--input"))
            input_enabled = 1;
        else if (!strcmp(argv[i], "--fb") && i + 1 < argc)
            fb_path = argv[++i];
        else if (!strcmp(argv[i], "--geometry") && i + 1 < argc)
            geometry = argv[++i];
        else if (!strcmp(argv[i], "--secret-file") && i + 1 < argc)
            secret_path = argv[++i];
        else
            usage(argv[0]);
    }
    if (!bind_address || !secret_path)
        usage(argv[0]);
    load_secret(secret_path);

    fb_fd = open(fb_path, O_RDONLY | O_CLOEXEC);
    if (fb_fd < 0)
        fatal("open framebuffer");
    if (fstat(fb_fd, &fb_stat) < 0)
        fatal("fstat framebuffer");
    if (S_ISREG(fb_stat.st_mode)) {
        /* Two kinds of regular file, told apart by positive evidence:
         * the IMCTLFB1 magic (#835, native-gui --backend mem) is
         * self-describing; anything else is the raw-pixel test harness
         * (#833/#838) and must say its geometry. There are no FBIOGET_*
         * ioctls to ask either way. */
        char magic[sizeof(MEMFB_MAGIC) - 1];

        if (pread(fb_fd, magic, sizeof(magic), 0) == (ssize_t)sizeof(magic) &&
            !memcmp(magic, MEMFB_MAGIC, sizeof(magic))) {
            memfb_describe(fb_fd, fb_stat.st_size, &var, &fix);
            pixel_offset = MEMFB_HEADER_BYTES;
        } else {
            /* A plain file stands in for /dev/fb0: the geometry is given
             * explicitly and the pixels are packed 32bpp rows, no padding. */
            memset(&var, 0, sizeof(var));
            memset(&fix, 0, sizeof(fix));
            if (!geometry ||
                sscanf(geometry, "%ux%u", &var.xres, &var.yres) != 2 ||
                !var.xres || !var.yres) {
                fprintf(stderr,
                        "imagectl-monitor: --fb FILE is not a memory framebuffer "
                        "(no " MEMFB_MAGIC " header) and no --geometry WxH was given\n");
                return EXIT_FAILURE;
            }
            var.bits_per_pixel = 32;
            var.red.length = var.green.length = var.blue.length = 8;
            fix.line_length = var.xres * 4;
            fix.smem_len = var.xres * var.yres * 4;
            if ((unsigned long long)fb_stat.st_size < fix.smem_len) {
                fprintf(stderr,
                        "imagectl-monitor: FATAL: %s holds %lld bytes, "
                        "geometry needs %u\n",
                        fb_path, (long long)fb_stat.st_size, fix.smem_len);
                return EXIT_FAILURE;
            }
        }
    } else if (ioctl(fb_fd, FBIOGET_VSCREENINFO, &var) < 0 ||
               ioctl(fb_fd, FBIOGET_FSCREENINFO, &fix) < 0) {
        fatal("FBIOGET_*SCREENINFO");
    }
    if (var.bits_per_pixel != 32 ||
        var.red.length != 8 || var.green.length != 8 ||
        var.blue.length != 8) {
        fprintf(stderr,
                "imagectl-monitor: FATAL: unsupported framebuffer: "
                "%ux%u, %u bpp, RGB lengths %u/%u/%u\n",
                var.xres, var.yres, var.bits_per_pixel,
                var.red.length, var.green.length, var.blue.length);
        return EXIT_FAILURE;
    }
    if (fix.line_length < var.xres * 4) {
        fprintf(stderr,
                "imagectl-monitor: FATAL: framebuffer stride %u < row %u\n",
                fix.line_length, var.xres * 4);
        return EXIT_FAILURE;
    }

    mapped_length = fix.smem_len;
    mapped = mmap(NULL, mapped_length, PROT_READ, MAP_SHARED, fb_fd, 0);
    if (mapped == MAP_FAILED)
        fatal("mmap framebuffer");
    if (pixel_offset)
        frame_seq = (const uint32_t *)(mapped +
                                       offsetof(struct memfb_header, seq));

    rfb_framebuffer = calloc((size_t)var.xres * var.yres, 4);
    if (!rfb_framebuffer)
        fatal("allocate RFB framebuffer");

    screen = rfbGetScreen(&argc, argv, var.xres, var.yres, 8, 3, 4);
    if (!screen)
        fatal("rfbGetScreen");
    screen->desktopName = "ImageCtl monitor";
    screen->frameBuffer = rfb_framebuffer;
    screen->alwaysShared = TRUE;
    /* #839: a non-NULL authPasswdData makes LibVNCServer offer security
     * type 2 *instead of* None; passwordCheck is what decides. The pointer
     * itself is never dereferenced by the library once passwordCheck is
     * ours -- it is the switch, not a list of passwords. */
    screen->authPasswdData = secret;
    screen->passwordCheck = secret_check;
    screen->newClientHook = new_client;
    screen->port = port;
    screen->ipv6port = 0;
    screen->listenInterface = inet_addr(bind_address);
    if (screen->listenInterface == INADDR_NONE) {
        fprintf(stderr, "imagectl-monitor: invalid bind address: %s\n",
                bind_address);
        return EXIT_FAILURE;
    }

    if (input_enabled) {
        input_fd = create_uinput();
        screen->kbdAddEvent = keyboard_event;
        screen->ptrAddEvent = pointer_event;
    } else {
        /* Absolute cloner invariant: no uinput fd, and callbacks that drop
         * every event. Never NULL: the library does not check (#833). */
        screen->kbdAddEvent = keyboard_event_ignored;
        screen->ptrAddEvent = pointer_event_ignored;
        fprintf(stderr, "imagectl-monitor: view-only mode; input disabled\n");
    }

    /* Power control travels over ClientCutText, independent of --input. */
    screen->setXCutText = text_event;

    signal(SIGINT, stop_handler);
    signal(SIGTERM, stop_handler);
    signal(SIGCHLD, SIG_IGN);       /* run_power(): the kernel reaps (#839) */
    rfbInitServer(screen);
    if (!rfbIsActive(screen)) {
        fprintf(stderr,
                "imagectl-monitor: FATAL: RFB server did not start on %s:%d\n",
                bind_address, port);
        return EXIT_FAILURE;
    }
    fprintf(stderr, "imagectl-monitor: RFB listening on %s:%d, %ux%u@%dfps\n",
            bind_address, port, var.xres, var.yres, fps);

    while (!stopping && rfbIsActive(screen)) {
        unsigned int y;
        uint32_t seq_before = 0;
        uint8_t *destination = (uint8_t *)rfb_framebuffer;
        const uint8_t *source =
            mapped + pixel_offset + (size_t)var.yoffset * fix.line_length +
            (size_t)var.xoffset * 4;

        /* Memory framebuffer: odd seq = the GUI is mid-frame; a seq that
         * moved during the copy = we read a torn frame. Either way keep
         * the last whole frame on the wire and try again next tick. */
        if (frame_seq) {
            seq_before = __atomic_load_n(frame_seq, __ATOMIC_ACQUIRE);
            if (seq_before & 1u) {
                rfbProcessEvents(screen, 1000000 / fps);
                continue;
            }
        }

        for (y = 0; y < var.yres; y++)
            memcpy(destination + (size_t)y * var.xres * 4,
                   source + (size_t)y * fix.line_length,
                   (size_t)var.xres * 4);

        if (frame_seq &&
            __atomic_load_n(frame_seq, __ATOMIC_ACQUIRE) != seq_before) {
            rfbProcessEvents(screen, 1000000 / fps);
            continue;
        }

        rfbMarkRectAsModified(screen, 0, 0, var.xres, var.yres);
        rfbProcessEvents(screen, 1000000 / fps);
    }

    /* Clients first, uinput second (#839): rfbScreenCleanup stops the
     * client threads that call emit_event; destroying the device while one
     * of them was still injecting was a write to a closed fd. */
    rfbScreenCleanup(screen);
    if (input_fd >= 0) {
        ioctl(input_fd, UI_DEV_DESTROY);
        close(input_fd);
        input_fd = -1;
    }
    munmap(mapped, mapped_length);
    close(fb_fd);
    free(rfb_framebuffer);
    return EXIT_SUCCESS;
}
