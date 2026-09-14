/*
 * imagectl-monitor -- expose /dev/fb0 over standard RFB using LibVNCServer.
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
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
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

static void emit_event(unsigned short type, unsigned short code, int value) {
    struct input_event event = {
        .type = type,
        .code = code,
        .value = value,
    };

    if (write(input_fd, &event, sizeof(event)) != sizeof(event))
        fatal("write /dev/uinput");
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
    emit_event(EV_KEY, (unsigned short)code, down ? 1 : 0);
    sync_input();
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
 * too): it is not a screen keystroke, and the only gate is the server's
 * admin-only monitor WebSocket, the same gate that reaches this RFB port.
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
     * fork leaves the machine up, which is the safe direction. */
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

static void usage(const char *program) {
    fprintf(stderr,
            "usage: %s --bind ADDRESS [--port PORT] [--fps FPS] [--input]\n",
            program);
    exit(EXIT_FAILURE);
}

int main(int argc, char **argv) {
    const char *bind_address = NULL;
    const char *fb_path = "/dev/fb0";
    struct fb_var_screeninfo var;
    struct fb_fix_screeninfo fix;
    rfbScreenInfoPtr screen;
    uint8_t *mapped;
    size_t mapped_length;
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
        else
            usage(argv[0]);
    }
    if (!bind_address)
        usage(argv[0]);

    fb_fd = open(fb_path, O_RDONLY | O_CLOEXEC);
    if (fb_fd < 0)
        fatal("open framebuffer");
    if (ioctl(fb_fd, FBIOGET_VSCREENINFO, &var) < 0 ||
        ioctl(fb_fd, FBIOGET_FSCREENINFO, &fix) < 0)
        fatal("FBIOGET_*SCREENINFO");
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

    rfb_framebuffer = calloc((size_t)var.xres * var.yres, 4);
    if (!rfb_framebuffer)
        fatal("allocate RFB framebuffer");

    screen = rfbGetScreen(&argc, argv, var.xres, var.yres, 8, 3, 4);
    if (!screen)
        fatal("rfbGetScreen");
    screen->desktopName = "ImageCtl monitor";
    screen->frameBuffer = rfb_framebuffer;
    screen->alwaysShared = TRUE;
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
        /* Absolute cloner invariant: no uinput fd and no input callbacks. */
        screen->kbdAddEvent = NULL;
        screen->ptrAddEvent = NULL;
        fprintf(stderr, "imagectl-monitor: view-only mode; input disabled\n");
    }

    /* Power control travels over ClientCutText, independent of --input. */
    screen->setXCutText = text_event;

    signal(SIGINT, stop_handler);
    signal(SIGTERM, stop_handler);
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
        uint8_t *destination = (uint8_t *)rfb_framebuffer;
        const uint8_t *source =
            mapped + (size_t)var.yoffset * fix.line_length +
            (size_t)var.xoffset * 4;

        for (y = 0; y < var.yres; y++)
            memcpy(destination + (size_t)y * var.xres * 4,
                   source + (size_t)y * fix.line_length,
                   (size_t)var.xres * 4);

        rfbMarkRectAsModified(screen, 0, 0, var.xres, var.yres);
        rfbProcessEvents(screen, 1000000 / fps);
    }

    if (input_fd >= 0) {
        ioctl(input_fd, UI_DEV_DESTROY);
        close(input_fd);
    }
    rfbScreenCleanup(screen);
    munmap(mapped, mapped_length);
    close(fb_fd);
    free(rfb_framebuffer);
    return EXIT_SUCCESS;
}
