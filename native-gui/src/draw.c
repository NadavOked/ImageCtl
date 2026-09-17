#include <math.h>
#include "draw.h"

void draw_set(cairo_t *cr, Rgb c)              { cairo_set_source_rgb(cr, c.r, c.g, c.b); }
void draw_set_a(cairo_t *cr, Rgb c, double a)  { cairo_set_source_rgba(cr, c.r, c.g, c.b, a); }

void draw_rrect_path(cairo_t *cr, Rect r, double rad) {
    double m = r.w < r.h ? r.w : r.h;
    if (rad > m / 2) rad = m / 2;
    if (rad < 0) rad = 0;
    cairo_new_sub_path(cr);
    cairo_arc(cr, r.x + r.w - rad, r.y + rad,       rad, -M_PI / 2, 0);
    cairo_arc(cr, r.x + r.w - rad, r.y + r.h - rad, rad, 0, M_PI / 2);
    cairo_arc(cr, r.x + rad,       r.y + r.h - rad, rad, M_PI / 2, M_PI);
    cairo_arc(cr, r.x + rad,       r.y + rad,       rad, M_PI, 3 * M_PI / 2);
    cairo_close_path(cr);
}

void draw_fill_rrect(cairo_t *cr, Rect r, double rad, Rgb c) {
    draw_rrect_path(cr, r, rad);
    draw_set(cr, c);
    cairo_fill(cr);
}

void draw_border_rrect(cairo_t *cr, Rect r, double rad, Rgb c, double lw) {
    Rect in = { r.x + lw / 2, r.y + lw / 2, r.w - lw, r.h - lw };
    draw_rrect_path(cr, in, rad - lw / 2);
    draw_set(cr, c);
    cairo_set_line_width(cr, lw);
    cairo_stroke(cr);
}

void draw_box_shadow(cairo_t *cr, Rect r, double rad, double dy,
                     double blur, double spread, Rgb c, double alpha) {
    /* CSS: the shadow box is the element grown by <spread>, offset by dy,
     * then blurred with a gaussian of sigma = blur/2. Approximate the blur
     * with N concentric layers, each a little larger and a little fainter.
     * Per-layer alpha is chosen so the stack sums to about <alpha> in the
     * centre (1-(1-a)^N ~= alpha). */
    const int N = 14;
    double half = blur / 2;
    double a = 1 - pow(1 - alpha, 1.0 / N);
    for (int i = N; i >= 1; i--) {
        double e = spread + half * i / N;           /* growth of this layer */
        Rect s = { r.x - e, r.y - e + dy, r.w + 2 * e, r.h + 2 * e };
        if (s.w <= 0 || s.h <= 0) continue;
        draw_rrect_path(cr, s, rad + e);
        draw_set_a(cr, c, a);
        cairo_fill(cr);
    }
}

void draw_station_background(cairo_t *cr, const Theme *t, double w, double h) {
    /* .native-screen linear-gradient(180deg). */
    cairo_pattern_t *p = cairo_pattern_create_linear(0, 0, 0, h);
    cairo_pattern_add_color_stop_rgb(p, 0, t->login_a.r, t->login_a.g, t->login_a.b);
    cairo_pattern_add_color_stop_rgb(p, 1, t->login_b.r, t->login_b.g, t->login_b.b);
    cairo_rectangle(cr, 0, 0, w, h);
    cairo_set_source(cr, p); cairo_fill(cr); cairo_pattern_destroy(p);
}

void draw_led(cairo_t *cr, double cx, double cy, double d, Rgb c) {
    /* box-shadow: 0 0 8px <c> -- a 5px dot blurred with sigma 4 peaks at
     * only ~0.2 alpha (its area is small against the kernel) and is gone by
     * 8px out. Four rings whose stacked alpha follows that curve: ~0.13 at
     * the dot's edge, ~0.07, ~0.035, ~0.012 at the rim. A flat 0.10 per
     * ring read as a green smudge next to the browser's faint glow. */
    static const double A[4] = { 0.055, 0.040, 0.025, 0.012 };
    for (int i = 4; i >= 1; i--) {
        cairo_arc(cr, cx, cy, d / 2 + 2.0 * i, 0, 2 * M_PI);
        draw_set_a(cr, c, A[i - 1]);
        cairo_fill(cr);
    }
    cairo_arc(cr, cx, cy, d / 2, 0, 2 * M_PI);
    draw_set(cr, c);
    cairo_fill(cr);
}
