#ifndef IMAGECTL_ROUND_WIDGETS_H
#define IMAGECTL_ROUND_WIDGETS_H
#include "widgets.h"
enum { ROWS_ROOM_SETUP, ROWS_ROOM_LIVE, ROWS_CLASS };

typedef struct {
    Text name, mac, state;
    double h;
    int dim, on;
} Row;

typedef struct {
    Row r[MAX_MACHINES]; int n;
    Text empty;                 /* the <p class="sub"> when there are no rows */
    double h;                   /* the whole .room-machines box */
} RowSet;

typedef struct { Text big, small; double drop, h; } ProgLine;


/* #872 (Nadav, 16/09): three colours, one rule, shared by the room grid and
 * the idle cloner list. failed_last (the on-disk mark of #845 -- it failed
 * the previous clone, proven) is red; fail (SMART overall-health FAILED,
 * what the BIOS shows) is orange and asks; everything else is green --
 * including unchecked, which says so in words, not in colour. warn no
 * longer comes from the agent; an old agent's warn reads as orange.
 * smart_level is the same rule as a number: 0 green, 1 orange, 2 red. */
int smart_level(const char *smart);
Rgb smart_color(const Theme *t, const char *smart);
/* The status span of room.js machineRows / classes.js renderLive, as Pango
 * markup (room-ok / room-bad / room-warn colours); <out> >= 640 bytes. */
void room_status_markup(const App *a, const Machine *m, int mode, char *out, size_t n);
void rows_make(App *, cairo_t *, double, int, const char *, RowSet *);
void rows_draw(App *, cairo_t *, double, double, double, RowSet *);
ProgLine progline_make(cairo_t *, const char *, const char *, double);
void progline_draw(cairo_t *, const Theme *, ProgLine *, double, double, double);
Text confirm_label(cairo_t *, const char *, int, double);
void foot_buttons(App *, cairo_t *, Rect, Text *, const int *, const int *, int);
int image_options(const App *, const char **, char (*)[168], int);
#endif
