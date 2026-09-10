#include <math.h>
#include <stdio.h>
#include <string.h>
#include "widgets.h"

/* ---- hit table ------------------------------------------------------------ */

void hit_add(App *a, Rect r, int id) {
    if (a->clip_on) {
        /* .sbody{overflow-y:auto}: what is drawn outside the body box is
         * not on screen, so it must not be clickable either. */
        double x0 = fmax(r.x, a->clip.x), y0 = fmax(r.y, a->clip.y);
        double x1 = fmin(r.x + r.w, a->clip.x + a->clip.w);
        double y1 = fmin(r.y + r.h, a->clip.y + a->clip.h);
        if (x1 <= x0 || y1 <= y0) return;
        r = (Rect){ x0, y0, x1 - x0, y1 - y0 };
    }
    if (a->nhits < MAX_HITS) { a->hits[a->nhits].r = r; a->hits[a->nhits].id = id; a->nhits++; }
}

int hovered(const App *a, Rect r) {
    return a->ptr_visible && rect_has(r, a->ptr_x, a->ptr_y);
}

double dmax(double x, double y) { return x > y ? x : y; }

/* ---- buttons -------------------------------------------------------------- */

Text btn_label(cairo_t *cr, const char *s) { return text_make(cr, FONT_SANS, 13.5, 500, s, 0, DIR_RTL); }
double btn_height(cairo_t *cr) { return 9 + text_line_height(cr, FONT_SANS, 13.5) + 9 + 2; }
double btn_width(const Text *label) { return 15 + label->w + 15 + 2; }

/* console.css .btn (border hair, surface, ink), .primary (ink/on-ink),
 * .danger (danger text, danger-line border), each with :hover */
void draw_btn(App *a, cairo_t *cr, Rect b, Text *label, int kind, int id) {
    const Theme *t = a->theme;
    int hov = hovered(a, b);
    Rgb bg, line, fg;
    if (kind == BTN_PRIMARY) {
        bg = hov ? t->ink_hover : t->ink; line = bg; fg = t->on_ink;
    } else {
        bg = hov ? t->btn_hover : t->surface;
        line = hov ? t->btn_hover_line : (kind == BTN_DANGER ? t->danger_line : t->hair);
        fg = kind == BTN_DANGER ? t->danger : t->ink;
    }
    draw_fill_rrect(cr, b, RADIUS_SM, bg);
    draw_border_rrect(cr, b, RADIUS_SM, line, 1);
    text_draw(cr, label, b.x + (b.w - label->w) / 2, b.y + (b.h - label->h) / 2, fg);
    hit_add(a, b, id);
}

/* ---- inputs --------------------------------------------------------------- */

double field_height(cairo_t *cr) { return 10 + text_line_height(cr, FONT_SANS, 14) + 10 + 2; }

static void field_box(App *a, cairo_t *cr, Rect r, int focus) {
    const Theme *t = a->theme;
    draw_fill_rrect(cr, r, RADIUS_SM, t->field);
    if (focus) {
        /* outline:2px solid indigo; outline-offset:1px; border-color:transparent
         * -> a 2px ring whose inner edge is 1px outside the box. */
        Rect o = { r.x - 3, r.y - 3, r.w + 6, r.h + 6 };
        draw_border_rrect(cr, o, RADIUS_SM + 3, t->indigo, 2);
    } else {
        draw_border_rrect(cr, r, RADIUS_SM, t->hair, 1);
    }
}

/* console.css input[type=text|password] + :focus outline; .pw-wrap input */
void draw_field(App *a, cairo_t *cr, Rect r, const char *value, int mask,
                const char *placeholder, int id) {
    const Theme *t = a->theme;
    int focus = a->focus == id;
    field_box(a, cr, r, focus);

    char shown[512];
    int empty = value[0] == 0;
    if (mask) {                                 /* one U+2022 per character */
        size_t n = 0;
        for (const char *p = value; *p && n + 4 < sizeof shown; p++) {
            if ((*p & 0xC0) == 0x80) continue;  /* UTF-8 continuation byte */
            memcpy(shown + n, "\xe2\x80\xa2", 3); n += 3;
        }
        shown[n] = 0;
    } else {
        snprintf(shown, sizeof shown, "%s", empty && placeholder ? placeholder : value);
    }

    /* padding 10px 13px; the password field keeps 62px clear at the inline
     * end (left, in RTL) for the eye button. */
    double pad_end = id == HIT_PASS ? 62 : 13;
    double inner_w = r.w - 13 - pad_end;
    Text v = text_make_field(cr, shown, (int)inner_w);
    double tx = r.x + pad_end, ty = r.y + (r.h - v.h) / 2;
    cairo_save(cr);
    draw_rrect_path(cr, r, RADIUS_SM); cairo_clip(cr);
    /* ::placeholder has no rule in the CSS; the browser's default is a mid
     * grey, and muted is the token nearest to it. */
    text_draw(cr, &v, tx, ty, empty && placeholder ? t->muted : t->ink);
    if (focus) {
        PangoRectangle strong;
        pango_layout_get_cursor_pos(v.layout, empty ? 0 : (int)strlen(shown), &strong, NULL);
        double cx = tx + strong.x / (double)PANGO_SCALE;
        if (empty) cx = tx + inner_w;           /* caret at the start edge of an empty RTL field */
        cairo_rectangle(cr, cx, ty + strong.y / (double)PANGO_SCALE, 1, strong.height / (double)PANGO_SCALE);
        draw_set(cr, t->ink); cairo_fill(cr);
    }
    cairo_restore(cr);
    text_free(&v);
    hit_add(a, r, id);
}

/* select: the same box as an input (console.css groups them), with the
 * browser's disclosure arrow at the inline end (left in RTL). */
void draw_select(App *a, cairo_t *cr, Rect r, const char *shown, int id) {
    const Theme *t = a->theme;
    field_box(a, cr, r, a->dd_open == id);
    double inner_w = r.w - 13 - 13 - 14;        /* 14 for the arrow */
    Text v = text_make_field(cr, shown, (int)inner_w);
    cairo_save(cr);
    draw_rrect_path(cr, r, RADIUS_SM); cairo_clip(cr);
    text_draw(cr, &v, r.x + 13 + 14, r.y + (r.h - v.h) / 2, t->ink);
    cairo_restore(cr);
    text_free(&v);
    double cx = r.x + 13 + 4, cy = r.y + r.h / 2;
    cairo_move_to(cr, cx - 4, cy - 2); cairo_line_to(cr, cx, cy + 2); cairo_line_to(cr, cx + 4, cy - 2);
    draw_set(cr, t->ink); cairo_set_line_width(cr, 1.5); cairo_stroke(cr);
    hit_add(a, r, id);
}

/* The list a <select> opens is the browser's own widget, not CSS; it is
 * drawn here in the page's language (surface, hair, r-sm, 14px rows). */
void draw_popup(App *a, cairo_t *cr, Rect anchor, const char *const *opts, int n, int sel) {
    const Theme *t = a->theme;
    double row_h = 8 + text_line_height(cr, FONT_SANS, 14) + 8;
    Rect box = { anchor.x, anchor.y + anchor.h + 2, anchor.w, n * row_h + 2 };
    draw_box_shadow(cr, box, RADIUS_SM, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, box, RADIUS_SM, t->surface);
    draw_border_rrect(cr, box, RADIUS_SM, t->hair, 1);
    cairo_save(cr);
    draw_rrect_path(cr, box, RADIUS_SM); cairo_clip(cr);
    for (int i = 0; i < n; i++) {
        Rect row = { box.x + 1, box.y + 1 + i * row_h, box.w - 2, row_h };
        if (i == sel) draw_fill_rrect(cr, row, 0, t->indigo_soft);
        else if (hovered(a, row)) draw_fill_rrect(cr, row, 0, t->hover);
        Text v = text_make_field(cr, opts[i], (int)(row.w - 26));
        text_draw(cr, &v, row.x + 13, row.y + (row.h - v.h) / 2, t->ink);
        text_free(&v);
        hit_add(a, row, HIT_OPTION_BASE + i);
    }
    cairo_restore(cr);
}

/* ---- text roles ------------------------------------------------------------ */

static Text rtl_block_make(cairo_t *cr, double px, int weight,
                           const char *s, double w) {
    Text text = text_make(cr, FONT_SANS, px, weight, s, (int)w, DIR_RTL);
    /* CSS block text inherits the document's RTL start edge: physically right. */
    pango_layout_set_alignment(text.layout, PANGO_ALIGN_RIGHT);
    return text;
}

Text label_make(cairo_t *cr, const char *s, double w) { return rtl_block_make(cr, 12, 500, s, w); }
Text sub_make(cairo_t *cr, const char *s, double w)   { return text_make(cr, FONT_SANS, 14, 400, s, (int)w, DIR_RTL); }
Text error_make(cairo_t *cr, const char *s, double w) { return text_make(cr, FONT_SANS, 12.5, 400, s, (int)w, DIR_RTL); }

/* ---- the card ---------------------------------------------------------------- */

Head head_make(cairo_t *cr, const char *h3, const char *p, double inner_w) {
    Head hd;
    hd.h3 = rtl_block_make(cr, 17, 600, h3, inner_w);
    hd.p  = rtl_block_make(cr, 12.5, 400, p, inner_w);
    hd.h  = 18 + hd.h3.h + 3 + hd.p.h + 14 + 1;     /* padding 18/14 + border 1 */
    return hd;
}

double card_width(double W) { return fmin(680, 0.94 * W); }

static void head_draw(cairo_t *cr, const Theme *t, Head *hd, Rect card) {
    double x = card.x + 22, y = card.y + 18;
    text_draw(cr, &hd->h3, x, y, t->ink);
    text_draw(cr, &hd->p,  x, y + hd->h3.h + 3, t->muted);
    cairo_rectangle(cr, card.x, card.y + hd->h - 1, card.w, 1);
    draw_set(cr, t->hair); cairo_fill(cr);
    text_free(&hd->h3); text_free(&hd->p);
}

Rect card_frame(App *a, cairo_t *cr, double W, double H, double head_h, Head *hd,
                double body_h, double foot_h, Rect *body, Rect *foot) {
    const Theme *t = a->theme;
    double cw = card_width(W), ch = hd->h + body_h + foot_h;
    if (ch > 0.92 * H) ch = 0.92 * H;                          /* .sheet max-height:92vh */
    /* .st-card centred in .st-main (padding 20) */
    Rect card = { (W - cw) / 2, head_h + (H - head_h - ch) / 2, cw, ch };
    if (card.y < head_h + 20) card.y = head_h + 20;
    /* box-shadow: 0 34px 80px -24px shadow-strong; radius 18; overflow hidden */
    draw_box_shadow(cr, card, 18, 34, 80, -24, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, card, 18, t->surface);
    cairo_save(cr);
    draw_rrect_path(cr, card, 18);
    cairo_clip(cr);
    head_draw(cr, t, hd, card);
    *body = (Rect){ card.x, card.y + hd->h, cw, ch - hd->h - foot_h };
    *foot = (Rect){ card.x, card.y + ch - foot_h, cw, foot_h };
    return card;
}

void card_end(App *a, cairo_t *cr) { (void)a; cairo_restore(cr); }

void body_clip_begin(App *a, cairo_t *cr, Rect body) {
    cairo_save(cr);
    cairo_rectangle(cr, body.x, body.y, body.w, body.h);
    cairo_clip(cr);
    a->clip = body; a->clip_on = 1;
}

void body_clip_end(App *a, cairo_t *cr) { a->clip_on = 0; cairo_restore(cr); }

double foot_height(cairo_t *cr, double content_h) { (void)cr; return 1 + 14 + content_h + 14; }

/* .sfoot: border-top hair, background sunken, padding 14px 22px */
void foot_draw_bg(cairo_t *cr, const Theme *t, Rect foot) {
    cairo_rectangle(cr, foot.x, foot.y, foot.w, foot.h);
    draw_set(cr, t->sunken); cairo_fill(cr);
    cairo_rectangle(cr, foot.x, foot.y, foot.w, 1);
    draw_set(cr, t->hair); cairo_fill(cr);
}

/* ---- station.css pieces ---------------------------------------------------------- */

/* .menu-card/.disk-card .tray (52x34, radius 6, ink, LED bottom 6 left 7)
 * and .tray-multi (two hair-coloured copies behind: 4px/-1, 8px/-2). */
void draw_tray(cairo_t *cr, const Theme *t, double x, double y, int multi) {
    if (multi) {
        draw_fill_rrect(cr, (Rect){ x + 8 + 2, y + 8 + 2, 52 - 4, 34 - 4 }, 4, t->hair);
        draw_fill_rrect(cr, (Rect){ x + 4 + 1, y + 4 + 1, 52 - 2, 34 - 2 }, 5, t->hair);
    }
    draw_fill_rrect(cr, (Rect){ x, y, 52, 34 }, 6, t->ink);
    draw_led(cr, x + 7 + 2.5, y + 34 - 6 - 2.5, 5, t->led_ok);
}

/* repeating-linear-gradient(110deg, a 0 <p1>, b <p1> <period>) as a cairo
 * pattern; b_alpha 0 makes the second band transparent. */
static void stripes(cairo_t *cr, Rect clip_r, Rgb a, double a_alpha, double p1,
                    Rgb b, double b_alpha, double period) {
    double ang = 110.0 * M_PI / 180.0, dx = sin(ang), dy = -cos(ang);
    cairo_pattern_t *p = cairo_pattern_create_linear(clip_r.x, clip_r.y,
                                                     clip_r.x + dx * period, clip_r.y + dy * period);
    double k = p1 / period;
    cairo_pattern_add_color_stop_rgba(p, 0, a.r, a.g, a.b, a_alpha);
    cairo_pattern_add_color_stop_rgba(p, k, a.r, a.g, a.b, a_alpha);
    cairo_pattern_add_color_stop_rgba(p, k, b.r, b.g, b.b, b_alpha);
    cairo_pattern_add_color_stop_rgba(p, 1, b.r, b.g, b.b, b_alpha);
    cairo_pattern_set_extend(p, CAIRO_EXTEND_REPEAT);
    cairo_rectangle(cr, clip_r.x, clip_r.y, clip_r.w, clip_r.h);
    cairo_set_source(cr, p);
    cairo_fill(cr);
    cairo_pattern_destroy(p);
}

/* console.css .bar (track, overflow hidden) + .bar i (led-write, radius 5);
 * station.css .big-bar (height 14, radius 8); progress.css for the two
 * unknown states. The indeterminate stripe is animated in the browser; here
 * it stands at its starting position. */
void draw_big_bar(cairo_t *cr, const Theme *t, Rect r, int pct, int moving) {
    draw_fill_rrect(cr, r, 8, t->track);
    cairo_save(cr);
    draw_rrect_path(cr, r, 8); cairo_clip(cr);
    if (pct >= 0) {
        double w = r.w * (pct > 100 ? 100 : pct) / 100.0;
        if (w > 0) draw_fill_rrect(cr, (Rect){ r.x, r.y, w, r.h }, 5, t->led_write);
    } else if (moving) {
        stripes(cr, (Rect){ r.x, r.y, r.w * 0.40, r.h }, STRIPE_A, 1, 8, STRIPE_B, 1, 16);
    } else {
        stripes(cr, r, STRIPE_IDLE, 0.25, 2, STRIPE_IDLE, 0, 8);
    }
    cairo_restore(cr);
}

/* .cls-bars: 4 x (height 3, radius 2, hair; .on ink), gap 5, flex:1 */
void draw_cls_bars(cairo_t *cr, const Theme *t, double x, double y, double w, int current) {
    double bw = (w - 3 * 5) / 4;
    for (int i = 0; i < 4; i++)
        draw_fill_rrect(cr, (Rect){ x + i * (bw + 5), y, bw, 3 }, 2, i < current ? t->ink : t->hair);
}

/* #toast: fixed bottom 22, centred; ink on on-ink; padding 10px 18px;
 * radius 10; 13px; box-shadow 0 10px 30px -8px shadow-strong */
void draw_toast(App *a, cairo_t *cr, double W, double H) {
    if (!a->toast[0]) return;
    const Theme *t = a->theme;
    Text v = text_make(cr, FONT_SANS, 13, 400, a->toast, (int)(W * 0.8), DIR_RTL);
    Rect b = { (W - (v.w + 36)) / 2, H - 22 - (v.h + 20), v.w + 36, v.h + 20 };
    draw_box_shadow(cr, b, 10, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, b, 10, t->ink);
    text_draw(cr, &v, b.x + 18, b.y + 10, t->on_ink);
    text_free(&v);
}

/* ---- ports of the JS helpers --------------------------------------------------------- */

void fmt_bytes(char *out, size_t n, double v) {
    static const char *U[] = { "B", "KB", "MB", "GB", "TB" };
    if (!(v > 0)) { snprintf(out, n, "0"); return; }
    int i = 0;
    while (v >= 1024 && i < 4) { v /= 1024; i++; }
    if (v >= 100) snprintf(out, n, "%.0f %s", v, U[i]);
    else          snprintf(out, n, "%.1f %s", v, U[i]);
}

void progress_label(char *out, size_t n, int pct, int moving, int partition) {
    if (pct >= 0) {
        if (partition > 0) snprintf(out, n, "%d%% · מחיצה %d (בלוקים)", pct > 100 ? 100 : pct, partition);
        else               snprintf(out, n, "%d%%", pct > 100 ? 100 : pct);
    } else {
        snprintf(out, n, "%s", moving ? "נקראו בייטים · הסך לא ידוע" : "טרם נקראו בייטים · הסך לא ידוע");
    }
}
