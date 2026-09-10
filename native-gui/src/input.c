#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/input.h>
#include "input.h"

#define MAX_DEV 16
#define QUEUE 64
#define BITS_PER_LONG (8 * sizeof(long))
#define NBITS(x) (((x) / BITS_PER_LONG) + 1)
#define test_bit(bit, arr) (((arr)[(bit) / BITS_PER_LONG] >> ((bit) % BITS_PER_LONG)) & 1)

enum { DEV_KEYBOARD = 1, DEV_MOUSE = 2, DEV_TOUCH = 4 };

typedef struct {
    int fd, kinds;
    int abs_min_x, abs_max_x, abs_min_y, abs_max_y;
    int shift;
} Dev;

struct Input {
    Dev dev[MAX_DEV]; int ndev;
    int w, h;
    double px, py;                          /* pointer */
    int moved, touch_moved;
    Event q[QUEUE]; int qh, qt;
};

/* US keymap: {code, plain, shifted}. Enough for an ASCII account. */
static const struct { int code; char n, s; } KEYMAP[] = {
    {KEY_1,'1','!'},{KEY_2,'2','@'},{KEY_3,'3','#'},{KEY_4,'4','$'},{KEY_5,'5','%'},
    {KEY_6,'6','^'},{KEY_7,'7','&'},{KEY_8,'8','*'},{KEY_9,'9','('},{KEY_0,'0',')'},
    {KEY_MINUS,'-','_'},{KEY_EQUAL,'=','+'},
    {KEY_Q,'q','Q'},{KEY_W,'w','W'},{KEY_E,'e','E'},{KEY_R,'r','R'},{KEY_T,'t','T'},
    {KEY_Y,'y','Y'},{KEY_U,'u','U'},{KEY_I,'i','I'},{KEY_O,'o','O'},{KEY_P,'p','P'},
    {KEY_LEFTBRACE,'[','{'},{KEY_RIGHTBRACE,']','}'},
    {KEY_A,'a','A'},{KEY_S,'s','S'},{KEY_D,'d','D'},{KEY_F,'f','F'},{KEY_G,'g','G'},
    {KEY_H,'h','H'},{KEY_J,'j','J'},{KEY_K,'k','K'},{KEY_L,'l','L'},
    {KEY_SEMICOLON,';',':'},{KEY_APOSTROPHE,'\'','"'},{KEY_GRAVE,'`','~'},{KEY_BACKSLASH,'\\','|'},
    {KEY_Z,'z','Z'},{KEY_X,'x','X'},{KEY_C,'c','C'},{KEY_V,'v','V'},{KEY_B,'b','B'},
    {KEY_N,'n','N'},{KEY_M,'m','M'},{KEY_COMMA,',','<'},{KEY_DOT,'.','>'},{KEY_SLASH,'/','?'},
    {KEY_SPACE,' ',' '},
    {KEY_KP0,'0','0'},{KEY_KP1,'1','1'},{KEY_KP2,'2','2'},{KEY_KP3,'3','3'},{KEY_KP4,'4','4'},
    {KEY_KP5,'5','5'},{KEY_KP6,'6','6'},{KEY_KP7,'7','7'},{KEY_KP8,'8','8'},{KEY_KP9,'9','9'},
    {KEY_KPDOT,'.','.'},{KEY_KPMINUS,'-','-'},{KEY_KPPLUS,'+','+'},{KEY_KPASTERISK,'*','*'},
    {KEY_KPSLASH,'/','/'},
};

static void push(Input *in, Event e) {
    int next = (in->qt + 1) % QUEUE;
    if (next == in->qh) return;                    /* full: drop, never block */
    in->q[in->qt] = e; in->qt = next;
}

static int probe(Input *in, const char *path) {
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    unsigned long ev[NBITS(EV_MAX)] = { 0 }, key[NBITS(KEY_MAX)] = { 0 };
    unsigned long rel[NBITS(REL_MAX)] = { 0 }, abs_[NBITS(ABS_MAX)] = { 0 };
    if (ioctl(fd, EVIOCGBIT(0, sizeof ev), ev) < 0) { close(fd); return -1; }
    Dev d = { .fd = fd };
    if (test_bit(EV_KEY, ev) && ioctl(fd, EVIOCGBIT(EV_KEY, sizeof key), key) >= 0) {
        if (test_bit(KEY_A, key) && test_bit(KEY_Z, key)) d.kinds |= DEV_KEYBOARD;
        if (test_bit(EV_REL, ev) && ioctl(fd, EVIOCGBIT(EV_REL, sizeof rel), rel) >= 0
            && test_bit(REL_X, rel) && test_bit(BTN_LEFT, key)) d.kinds |= DEV_MOUSE;
        if (test_bit(EV_ABS, ev) && ioctl(fd, EVIOCGBIT(EV_ABS, sizeof abs_), abs_) >= 0
            && (test_bit(ABS_X, abs_) || test_bit(ABS_MT_POSITION_X, abs_))
            && (test_bit(BTN_TOUCH, key) || test_bit(BTN_LEFT, key))) {
            struct input_absinfo ax, ay;
            int cx = test_bit(ABS_MT_POSITION_X, abs_) ? ABS_MT_POSITION_X : ABS_X;
            int cy = test_bit(ABS_MT_POSITION_Y, abs_) ? ABS_MT_POSITION_Y : ABS_Y;
            if (ioctl(fd, EVIOCGABS(cx), &ax) >= 0 && ioctl(fd, EVIOCGABS(cy), &ay) >= 0
                && ax.maximum > ax.minimum && ay.maximum > ay.minimum) {
                d.abs_min_x = ax.minimum; d.abs_max_x = ax.maximum;
                d.abs_min_y = ay.minimum; d.abs_max_y = ay.maximum;
                d.kinds |= DEV_TOUCH;
            }
        }
    }
    if (!d.kinds) { close(fd); return -1; }
    if (ioctl(fd, EVIOCGRAB, 1) < 0)
        fprintf(stderr, "native-gui: %s: EVIOCGRAB: %s (keys will also reach the text console)\n", path, strerror(errno));
    in->dev[in->ndev++] = d;
    return 0;
}

Input *input_open(int screen_w, int screen_h) {
    Input *in = calloc(1, sizeof *in);
    in->w = screen_w; in->h = screen_h;
    in->px = screen_w / 2.0; in->py = screen_h / 2.0;
    char path[64];
    for (int i = 0; i < 32 && in->ndev < MAX_DEV; i++) {
        snprintf(path, sizeof path, "/dev/input/event%d", i);
        probe(in, path);
    }
    if (!in->ndev) { free(in); return NULL; }
    int kinds = 0;
    for (int i = 0; i < in->ndev; i++) kinds |= in->dev[i].kinds;
    fprintf(stderr, "native-gui: input: %d device(s):%s%s%s\n", in->ndev,
            kinds & DEV_KEYBOARD ? " keyboard" : "", kinds & DEV_MOUSE ? " mouse" : "",
            kinds & DEV_TOUCH ? " touch" : "");
    return in;
}

int input_fill_pollfds(Input *in, struct pollfd *out, int max) {
    int n = 0;
    for (int i = 0; i < in->ndev && n < max; i++, n++) {
        out[n].fd = in->dev[i].fd; out[n].events = POLLIN; out[n].revents = 0;
    }
    return n;
}

static void key_event(Input *in, Dev *d, int code, int value) {
    if (code == KEY_LEFTSHIFT || code == KEY_RIGHTSHIFT) { d->shift = value != 0; return; }
    if (value == 0) return;                          /* release */
    Event e = { 0 };
    switch (code) {
    case KEY_TAB:       e.type = UIEV_KEY; e.key = KEYSYM_TAB; push(in, e); return;
    case KEY_ENTER: case KEY_KPENTER:
                        e.type = UIEV_KEY; e.key = KEYSYM_ENTER; push(in, e); return;
    case KEY_BACKSPACE: e.type = UIEV_KEY; e.key = KEYSYM_BACKSPACE; push(in, e); return;
    case KEY_ESC:       e.type = UIEV_KEY; e.key = KEYSYM_ESC; push(in, e); return;
    }
    for (size_t i = 0; i < sizeof KEYMAP / sizeof KEYMAP[0]; i++)
        if (KEYMAP[i].code == code) {
            e.type = UIEV_CHAR; e.ch = d->shift ? KEYMAP[i].s : KEYMAP[i].n;
            push(in, e); return;
        }
}

static void clamp(Input *in) {
    if (in->px < 0) in->px = 0;
    if (in->px > in->w - 1) in->px = in->w - 1;
    if (in->py < 0) in->py = 0;
    if (in->py > in->h - 1) in->py = in->h - 1;
}

static void read_dev(Input *in, Dev *d) {
    struct input_event ev[32];
    for (;;) {
        ssize_t r = read(d->fd, ev, sizeof ev);
        if (r <= 0) return;                          /* EAGAIN: drained */
        int n = (int)(r / sizeof ev[0]);
        for (int i = 0; i < n; i++) {
            switch (ev[i].type) {
            case EV_KEY:
                if (ev[i].code == BTN_LEFT || ev[i].code == BTN_TOUCH) {
                    if (ev[i].value == 1) {
                        Event e = { .type = UIEV_CLICK, .x = in->px, .y = in->py,
                                    .is_touch = (d->kinds & DEV_TOUCH) != 0 };
                        push(in, e);
                    }
                } else if (d->kinds & DEV_KEYBOARD) key_event(in, d, ev[i].code, ev[i].value);
                break;
            case EV_REL:
                if (ev[i].code == REL_X) { in->px += ev[i].value; in->moved = 1; }
                if (ev[i].code == REL_Y) { in->py += ev[i].value; in->moved = 1; }
                break;
            case EV_ABS:
                if (ev[i].code == ABS_X || ev[i].code == ABS_MT_POSITION_X) {
                    in->px = (ev[i].value - d->abs_min_x) * (double)in->w / (d->abs_max_x - d->abs_min_x);
                    in->touch_moved = 1;
                }
                if (ev[i].code == ABS_Y || ev[i].code == ABS_MT_POSITION_Y) {
                    in->py = (ev[i].value - d->abs_min_y) * (double)in->h / (d->abs_max_y - d->abs_min_y);
                    in->touch_moved = 1;
                }
                break;
            case EV_SYN:
                if (in->moved || in->touch_moved) {
                    clamp(in);
                    Event e = { .type = UIEV_MOVE, .x = in->px, .y = in->py, .is_touch = in->touch_moved && !in->moved };
                    push(in, e);
                    in->moved = in->touch_moved = 0;
                }
                break;
            }
        }
    }
}

void input_pump(Input *in, const struct pollfd *fds, int n) {
    for (int i = 0; i < n; i++) {
        if (!(fds[i].revents & POLLIN)) continue;
        for (int j = 0; j < in->ndev; j++)
            if (in->dev[j].fd == fds[i].fd) read_dev(in, &in->dev[j]);
    }
}

int input_next(Input *in, Event *out) {
    if (in->qh == in->qt) return 0;
    *out = in->q[in->qh]; in->qh = (in->qh + 1) % QUEUE;
    return 1;
}

void input_close(Input *in) {
    if (!in) return;
    for (int i = 0; i < in->ndev; i++) { ioctl(in->dev[i].fd, EVIOCGRAB, 0); close(in->dev[i].fd); }
    free(in);
}
