/* The ImageSrv logo -- see brand.h. Every number below is read from the
 * approved files in C:\ImageCtl-Lab\brand-final (#1302, Nadav 12/10);
 * tests/test_native_brand.py binds them to the SVG text it was copied from. */
#include <math.h>
#include "brand.h"
#include "text.h"

#define HEX(v) { (((v)>>16)&255)/255.0, (((v)>>8)&255)/255.0, ((v)&255)/255.0 }

/* logo-lockup.svg (light background) and logo-lockup-dark.svg. These are the
 * logo's own colours, not console tokens: the brand blue of a dark background
 * (#3FA4DA) is not a console colour, so they live here and not in theme.c. */
static const Rgb BRAND_BLUE = HEX(0x0079B8), BRAND_INK = HEX(0x1D2730), BRAND_TAG = HEX(0x5B6670);
static const Rgb BRAND_BLUE_DARK = HEX(0x3FA4DA), BRAND_INK_DARK = HEX(0xF2F5F7), BRAND_TAG_DARK = HEX(0xA7B1B9);

/* The pattern, row by row: which of the three columns is lit (logo.svg). */
static const int PATTERN[3][3] = { {1,1,0}, {0,1,1}, {1,0,1} };

static int gap_of(int cell)   { int g = (int)lround(cell / 3.0); return g < 1 ? 1 : g; }  /* 6 -> 2, 3 -> 1 */
static int group_of(int cell) { return (int)lround(cell * 4.0 / 3.0); }                  /* 6 -> 8, 3 -> 4 */

double brand_symbol_width(int cell)  { return 6 * cell + 4 * gap_of(cell) + group_of(cell); }
double brand_symbol_height(int cell) { return 3 * cell + 2 * gap_of(cell); }

void draw_brand_symbol(cairo_t *cr, double x, double y, int cell, Rgb color) {
    const int g = gap_of(cell), pitch = cell + g, copy = 3 * cell + 2 * g + group_of(cell);
    const double rx = cell >= 6 ? cell / 8.0 : 0;   /* rx 0.75 of 6; square below that, as in the PNGs */
    x = round(x); y = round(y);
    for (int half = 0; half < 2; half++)
        for (int row = 0; row < 3; row++)
            for (int col = 0; col < 3; col++)
                if (PATTERN[row][col])
                    draw_fill_rrect(cr, (Rect){ x + half * copy + col * pitch, y + row * pitch, cell, cell }, rx, color);
}

/* The word, in the path units of logo-lockup.svg's inner <g>: stroke 5.4,
 * round caps and joins; the outer transform is translate(94 2.8) scale(.92)
 * with the symbol 76 wide from y 26.78 (compact) -- 33 units tall. */
static void path_image(cairo_t *cr) {
    cairo_move_to(cr, 4, 28);    cairo_line_to(cr, 4, 60);                         /* I */
    cairo_move_to(cr, 14, 60);   cairo_line_to(cr, 14, 38);                        /* m */
    cairo_move_to(cr, 14, 46.5); cairo_curve_to(cr, 14, 41.5, 17, 38, 21.75, 38);
    cairo_curve_to(cr, 26.5, 38, 29.5, 41.5, 29.5, 46.5); cairo_line_to(cr, 29.5, 60);
    cairo_move_to(cr, 29.5, 46.5); cairo_curve_to(cr, 29.5, 41.5, 32.5, 38, 37.25, 38);
    cairo_curve_to(cr, 42, 38, 45, 41.5, 45, 46.5); cairo_line_to(cr, 45, 60);
    cairo_move_to(cr, 75, 38);   cairo_line_to(cr, 75, 60);                        /* a */
    cairo_new_sub_path(cr);      cairo_arc(cr, 64, 49, 11, 0, 2 * M_PI);
    cairo_move_to(cr, 106, 38);  cairo_line_to(cr, 106, 63.5);                     /* g */
    cairo_curve_to(cr, 106, 71, 101, 75.5, 95, 75.5); cairo_curve_to(cr, 91, 75.5, 87.5, 74, 85.5, 71.5);
    cairo_new_sub_path(cr);      cairo_arc(cr, 95, 49, 11, 0, 2 * M_PI);
    cairo_move_to(cr, 115, 49);  cairo_line_to(cr, 137, 49);                       /* e: A11 11 0 1 0 */
    cairo_arc_negative(cr, 126, 49, 11, 0, atan2(56.1 - 49, 134.4 - 126));
}

static void path_srv(cairo_t *cr) {
    cairo_move_to(cr, 167.5, 33.5);                                                /* S */
    cairo_curve_to(cr, 165.3, 30, 161.6, 28, 157, 28);
    cairo_curve_to(cr, 150.9, 28, 146.6, 31.6, 146.6, 36.4);
    cairo_curve_to(cr, 146.6, 41.8, 151.2, 43.4, 157, 44.4);
    cairo_curve_to(cr, 163.4, 45.5, 168.2, 47.4, 168.2, 53);
    cairo_curve_to(cr, 168.2, 57.6, 163.6, 60.6, 157.2, 60.6);
    cairo_curve_to(cr, 152, 60.6, 147.8, 58.4, 145.6, 55);
    cairo_move_to(cr, 178.5, 60); cairo_line_to(cr, 178.5, 38);                    /* r */
    cairo_move_to(cr, 178.5, 47); cairo_curve_to(cr, 178.5, 41.4, 182, 38, 188.5, 38);
    cairo_move_to(cr, 195, 38);   cairo_line_to(cr, 203.5, 60); cairo_line_to(cr, 212, 38);   /* v */
}

#define WORD_GAP 18.0      /* 94 - 76: symbol's right edge to the word's origin */
#define WORD_SCALE .92
#define WORD_RIGHT 214.7   /* the v's last point + half the stroke */
#define WORD_Y 2.8         /* the word's origin, in the lockup's own units */
#define SYMBOL_Y 26.78     /* the symbol's top in logo-lockup-compact.svg ... */
#define SYMBOL_Y_TAG 45.28 /* ... and in logo-lockup.svg, centred on word + tagline */
#define TAG_BASELINE 95.0
#define SYMBOL_UNITS 33.0

static double unit(int cell) { return brand_symbol_height(cell) / SYMBOL_UNITS; }

double brand_lockup_width(int cell) {
    return brand_symbol_width(cell) + unit(cell) * (WORD_GAP + WORD_SCALE * WORD_RIGHT);
}

double draw_brand_lockup_r(cairo_t *cr, double right, double top, int cell, int on_dark, int tagline) {
    const double k = unit(cell), left = round(right - brand_lockup_width(cell));
    const double sy = tagline ? SYMBOL_Y_TAG : SYMBOL_Y;   /* lockup units -> px: top + k * (u - sy) */
    top = round(top);
    draw_brand_symbol(cr, left, top, cell, on_dark ? BRAND_BLUE_DARK : BRAND_BLUE);
    cairo_save(cr);
    cairo_translate(cr, left + brand_symbol_width(cell) + k * WORD_GAP, top + k * (WORD_Y - sy));
    cairo_scale(cr, k * WORD_SCALE, k * WORD_SCALE);
    cairo_set_line_width(cr, 5.4);
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
    cairo_new_path(cr); path_image(cr); draw_set(cr, on_dark ? BRAND_INK_DARK : BRAND_INK); cairo_stroke(cr);
    path_srv(cr); draw_set(cr, on_dark ? BRAND_BLUE_DARK : BRAND_BLUE); cairo_stroke(cr);
    cairo_restore(cr);
    if (tagline) {   /* <text x=290.8 y=95 font-size=17 font-weight=500 text-anchor=end>;
                      * the word ends at 94 + .92 * 214.7 = 291.5 */
        Text tag = text_make(cr, FONT_SANS, 17 * k, 500, "שרת אימג'ים", 0, DIR_RTL);
        text_draw_r(cr, &tag, right - k * (291.5 - 290.8), top + k * (TAG_BASELINE - sy) - text_baseline(&tag),
                    on_dark ? BRAND_TAG_DARK : BRAND_TAG);
        text_free(&tag);
    }
    return left;
}
