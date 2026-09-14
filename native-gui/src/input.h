/* Keyboard and pointer straight from evdev (/dev/input/event*): no
 * libinput, no xkbcommon. The login form takes an ASCII console account
 * name and password, so a fixed US keymap is enough; Hebrew typing is not
 * needed on these two screens and is not attempted. Keyboards are grabbed
 * (EVIOCGRAB) so the password does not also echo on the text VT. */
#ifndef IMAGECTL_INPUT_H
#define IMAGECTL_INPUT_H

#include <poll.h>

typedef enum { UIEV_NONE = 0, UIEV_CHAR, UIEV_KEY, UIEV_MOVE, UIEV_CLICK } EvType;
enum { KEYSYM_TAB = 1, KEYSYM_ENTER, KEYSYM_BACKSPACE, KEYSYM_ESC };

typedef struct {
    EvType type;
    int ch;             /* EV_CHAR: printable ASCII */
    int key;            /* EV_KEY: KEYSYM_* */
    double x, y;        /* EV_MOVE / EV_CLICK, screen pixels */
    int is_touch;       /* absolute device: no cursor to draw */
} Event;

typedef struct Input Input;

/* Opens every keyboard, mouse and touch device it finds. Always returns a
 * usable handle -- even with no device yet: a USB keyboard/mouse can appear a
 * second or two after boot, and input_rescan() picks it up. Returns NULL only
 * on allocation failure. */
Input *input_open(int screen_w, int screen_h);
/* Re-scan /dev/input for devices that appeared since (hot-plug, or a late USB
 * keyboard). Self-throttled to at most once a second; cheap to call every
 * loop. Already-open devices are left untouched. */
void input_rescan(Input *in);
int input_fill_pollfds(Input *in, struct pollfd *out, int max);
/* Read whatever is ready; then drain with input_next(). */
void input_pump(Input *in, const struct pollfd *fds, int n);
int input_next(Input *in, Event *out);
void input_close(Input *in);

#endif
