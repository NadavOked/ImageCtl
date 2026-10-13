/* The graphical server screen (#1203) -- drawing. Every number is the
 * approved mockup's CSS (C:\ImageCtl-Lab\design-approved\dcui\dcui.html,
 * Nadav 12/10), cited by its selector; ".sm" is its 1024x768 variant.
 * The colours are theme.c's tokens, which the mockup copied.
 *
 * Text direction: the page is RTL like <html dir="rtl"> -- base direction
 * RTL with auto-direction OFF, so "DHCP ו־TFTP לרשת ההפצה" keeps DHCP at the
 * right as the browser does (text.c's auto-dir would flip that paragraph to
 * LTR on its first letter). Addresses, names and fingerprints are LTR runs. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "brand.h"
#include "dcui.h"
#include "draw.h"
#include "text.h"

typedef struct { cairo_t *cr; const Theme *t; int sm, dark; } G;

static const Rgb DARK_ON = { 11 / 255.0, 17 / 255.0, 21 / 255.0 };   /* #0b1115: text on a filled colour, dark */
static const Rgb WHITE = { 1, 1, 1 };
static Rgb on_fill(const G *g) { return g->dark ? DARK_ON : WHITE; }

/* ---- text ----------------------------------------------------------------- */

#define MIN_PX 11.0   /* text.c TEXT_MIN_PX: nothing below 11px (mockup 10/10) */

static Text mk(G *g, Font font, double px, int weight, const char *s, double width, int ltr, int markup) {
    Text t;
    t.layout = pango_cairo_create_layout(g->cr);
    pango_context_set_base_dir(pango_layout_get_context(t.layout),
                               ltr ? PANGO_DIRECTION_LTR : PANGO_DIRECTION_RTL);
    pango_layout_set_auto_dir(t.layout, FALSE);
    pango_layout_context_changed(t.layout);
    PangoFontDescription *d = pango_font_description_new();
    pango_font_description_set_family(d, font == FONT_MONO ? FONT_MONO_FAMILY : FONT_SANS_FAMILY);
    pango_font_description_set_absolute_size(d, (px < MIN_PX ? MIN_PX : px) * PANGO_SCALE);
    pango_font_description_set_weight(d, weight);
    pango_layout_set_font_description(t.layout, d);
    pango_font_description_free(d);
    if (markup) pango_layout_set_markup(t.layout, s, -1);
    else pango_layout_set_text(t.layout, s, -1);
    if (width > 0) {
        pango_layout_set_width(t.layout, (int)(width * PANGO_SCALE));
        pango_layout_set_wrap(t.layout, PANGO_WRAP_WORD_CHAR);
        /* auto-dir off: LEFT/RIGHT are physical -- RTL text starts at the right */
        pango_layout_set_alignment(t.layout, ltr ? PANGO_ALIGN_LEFT : PANGO_ALIGN_RIGHT);
    }
    pango_layout_get_pixel_size(t.layout, &t.w, &t.h);
    return t;
}
static Text rtl(G *g, double px, int weight, const char *s, double width) { return mk(g, FONT_SANS, px, weight, s, width, 0, 0); }
static Text rtlm(G *g, double px, int weight, const char *s, double width) { return mk(g, FONT_SANS, px, weight, s, width, 0, 1); }
static Text mono(G *g, double px, int weight, const char *s) { return mk(g, FONT_MONO, px, weight, s, 0, 1, 0); }

/* Markup from the backend says <b>…</b>; the mockup's bold is also the ink
 * colour inside muted text (.hero .sub b, .na b, .hint b, .note b). */
static void ink_bold(const G *g, const char *src, char *out, size_t n) {
    char span[96];
    snprintf(span, sizeof span, "<span weight=\"600\" foreground=\"#%02X%02X%02X\">",
             (int)lround(g->t->ink.r * 255), (int)lround(g->t->ink.g * 255), (int)lround(g->t->ink.b * 255));
    size_t o = 0;
    for (const char *p = src; *p && o + 1 < n; ) {
        const char *rep = NULL; size_t skip = 0;
        if (!strncmp(p, "<b>", 3)) { rep = span; skip = 3; }
        else if (!strncmp(p, "</b>", 4)) { rep = "</span>"; skip = 4; }
        if (rep) {
            size_t l = strlen(rep);
            if (o + l >= n) break;
            memcpy(out + o, rep, l); o += l; p += skip;
        } else out[o++] = *p++;
    }
    out[o] = 0;
}
static Text rtlm_ink(G *g, double px, int weight, const char *s, double width) {
    char buf[4096];
    ink_bold(g, s, buf, sizeof buf);
    return mk(g, FONT_SANS, px, weight, buf, width, 0, 1);
}

static void put_r(G *g, Text *t, double right, double y, Rgb c) { text_draw_r(g->cr, t, right, y, c); }
static void put_l(G *g, Text *t, double x, double y, Rgb c) {
    PangoRectangle lg;
    pango_layout_get_pixel_extents(t->layout, NULL, &lg);
    text_draw(g->cr, t, x - lg.x, y, c);
}

/* ---- small pieces ----------------------------------------------------------- */

static Rgb pill_bg(const G *g, const char *k) {
    const Theme *t = g->t;
    if (!strcmp(k, "ok")) return t->success_soft;
    if (!strcmp(k, "warn")) return t->warning_soft;
    if (!strcmp(k, "bad")) return t->danger;
    if (!strcmp(k, "info")) return t->info_soft;
    return t->hover;
}
static Rgb pill_fg(const G *g, const char *k) {
    const Theme *t = g->t;
    if (!strcmp(k, "ok")) return t->led_ok;
    if (!strcmp(k, "warn")) return t->warn;
    if (!strcmp(k, "bad")) return on_fill(g);
    if (!strcmp(k, "info")) return t->info;
    return t->muted;
}

/* .pill: 24px high, 0 10px, radius 12, 12.5px 600, a 7px dot, gap 6 */
static double pill_w(G *g, const char *label) {
    Text l = rtl(g, 12.5, 600, label, 0);
    double w = 10 + 7 + 6 + l.w + 10;
    text_free(&l);
    return w;
}
static double pill(G *g, double x, double cy, const char *kind, const char *label) {
    if (!label[0]) return 0;
    Text l = rtl(g, 12.5, 600, label, 0);
    double w = 10 + 7 + 6 + l.w + 10;
    Rgb fg = pill_fg(g, kind);
    draw_fill_rrect(g->cr, (Rect){ x, cy - 12, w, 24 }, 12, pill_bg(g, kind));
    cairo_arc(g->cr, x + w - 10 - 3.5, cy, 3.5, 0, 2 * M_PI);
    draw_set(g->cr, fg); cairo_fill(g->cr);
    put_r(g, &l, x + w - 10 - 7 - 6, cy - l.h / 2.0, fg);
    text_free(&l);
    return w;
}

/* .kc: 600 11.5px mono, 20 high, min 24 wide, 0 5px, 1px border + 2px bottom, radius 4 */
typedef struct { Rgb bg, line, fg; double h, px; } KcStyle;
static KcStyle kc_plain(const G *g) { return (KcStyle){ g->t->surface, g->t->hair, g->t->ink, 20, 11.5 }; }
static double kc_w(G *g, const char *cap, double px) {
    Text c = mono(g, px, 600, cap);
    double w = fmax(24, c.w + 10);
    text_free(&c);
    return w;
}
static double kc(G *g, double right, double cy, const char *cap, KcStyle s) {
    Text c = mono(g, s.px, 600, cap);
    double w = fmax(24, c.w + 10), x = right - w, y = cy - s.h / 2;
    draw_fill_rrect(g->cr, (Rect){ x, y, w, s.h }, 4, s.line);                 /* the 2px bottom edge */
    draw_fill_rrect(g->cr, (Rect){ x + 1, y + 1, w - 2, s.h - 3 }, 3, s.bg);
    text_draw(g->cr, &c, x + (w - c.w) / 2, y + (s.h - 1 - c.h) / 2, s.fg);
    text_free(&c);
    return w;
}
/* several caps "→ ←": drawn right to left, 6px apart; returns the width */
static double kcs(G *g, double right, double cy, const char *caps, KcStyle s, int draw) {
    char buf[32]; snprintf(buf, sizeof buf, "%s", caps);
    double x = right, w = 0;
    for (char *save = NULL, *tok = strtok_r(buf, " ", &save); tok; tok = strtok_r(NULL, " ", &save)) {
        double cw = draw ? kc(g, x, cy, tok, s) : kc_w(g, tok, s.px);
        if (w > 0) w += 6;
        w += cw; x -= cw + 6;
    }
    return w;
}

static void stroke_style(cairo_t *cr, double lw) {
    cairo_set_line_width(cr, lw);
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
}

/* the mockup's lock: viewBox 16, rect 3,7 10x7 rx1.5 + the shackle; stroke 1.8 */
static void icon_lock(G *g, double x, double y, double size, Rgb c) {
    cairo_t *cr = g->cr;
    cairo_save(cr); cairo_translate(cr, x, y); cairo_scale(cr, size / 16, size / 16);
    stroke_style(cr, 1.8);
    draw_rrect_path(cr, (Rect){ 3, 7, 10, 7 }, 1.5);
    cairo_move_to(cr, 5.5, 7); cairo_line_to(cr, 5.5, 5);
    cairo_arc(cr, 8, 5, 2.5, M_PI, 2 * M_PI);
    cairo_line_to(cr, 10.5, 7);
    draw_set(cr, c); cairo_stroke(cr);
    cairo_restore(cr);
}

/* the four settings icons, viewBox 24, stroke 1.7 */
static void icon_menu(G *g, Rect box, const char *kind, Rgb c) {
    cairo_t *cr = g->cr;
    cairo_save(cr); cairo_translate(cr, box.x, box.y); cairo_scale(cr, box.w / 24, box.h / 24);
    stroke_style(cr, 1.7);
    cairo_new_path(cr);
    if (!strcmp(kind, "net")) {
        draw_rrect_path(cr, (Rect){ 3, 4, 18, 12 }, 2);
        cairo_move_to(cr, 8, 20); cairo_line_to(cr, 16, 20);
        cairo_move_to(cr, 12, 16); cairo_line_to(cr, 12, 20);
    } else if (!strcmp(kind, "rnet")) {
        cairo_arc(cr, 12, 12, 8, 0, atan2(6.4 - 12, 17.7 - 12) + 2 * M_PI);
        cairo_move_to(cr, 20, 4); cairo_line_to(cr, 20, 8); cairo_line_to(cr, 16, 8);
    } else if (!strcmp(kind, "svc")) {
        draw_rrect_path(cr, (Rect){ 4, 4, 16, 6 }, 1.5);
        draw_rrect_path(cr, (Rect){ 4, 14, 16, 6 }, 1.5);
        cairo_move_to(cr, 8, 7); cairo_line_to(cr, 8.01, 7);
        cairo_move_to(cr, 8, 17); cairo_line_to(cr, 8.01, 17);
    } else {                                                   /* cert: the shield and a tick */
        cairo_move_to(cr, 12, 3); cairo_line_to(cr, 19, 6); cairo_line_to(cr, 19, 11);
        cairo_curve_to(cr, 19, 16, 16, 19, 12, 21);
        cairo_curve_to(cr, 8, 19, 5, 16, 5, 11);
        cairo_line_to(cr, 5, 6); cairo_close_path(cr);
        cairo_move_to(cr, 9, 12); cairo_line_to(cr, 11, 14); cairo_line_to(cr, 15, 10);
    }
    draw_set(cr, c); cairo_stroke(cr);
    cairo_restore(cr);
}

/* .hero .gl: a 56px disc, the 30-unit glyph inside at 30px, stroke 3 */
static void hero_glyph(G *g, double x, double y, const char *kind) {
    cairo_t *cr = g->cr;
    Rgb bg = !strcmp(kind, "bad") ? g->t->danger : !strcmp(kind, "wait") ? g->t->info : g->t->led_ok;
    cairo_arc(cr, x + 28, y + 28, 28, 0, 2 * M_PI); draw_set(cr, bg); cairo_fill(cr);
    cairo_save(cr); cairo_translate(cr, x + 13, y + 13);
    stroke_style(cr, 3);
    cairo_new_path(cr);
    if (!strcmp(kind, "bad")) {
        cairo_move_to(cr, 15, 8); cairo_line_to(cr, 15, 17);
        cairo_move_to(cr, 15, 22); cairo_line_to(cr, 15, 22.5);
    } else if (!strcmp(kind, "wait")) {
        cairo_arc(cr, 15, 15, 9, 0, 2 * M_PI);
        cairo_move_to(cr, 15, 10); cairo_line_to(cr, 15, 15); cairo_line_to(cr, 18.5, 17.5);
    } else {
        cairo_move_to(cr, 8, 15.5); cairo_line_to(cr, 13, 20.5); cairo_line_to(cr, 22.5, 9.5);
    }
    draw_set(cr, on_fill(g)); cairo_stroke(cr);
    cairo_restore(cr);
}

static void hline(G *g, double x, double y, double w, Rgb c) {
    cairo_rectangle(g->cr, x, y, w, 1); draw_set(g->cr, c); cairo_fill(g->cr);
}

/* ---- header (.hd) and status line (.sb) -------------------------------------- */

static void header(G *g, const DcuiFrame *f, double W) {
    cairo_t *cr = g->cr;
    const double H = 48, pad = g->sm ? 16 : 24;
    draw_fill_rrect(cr, (Rect){ 0, 0, W, H }, 0, g->t->header);
    double right = W - pad;
    /* the compact dark lockup, 22px tall (.hd img): 4px cells */
    right = draw_brand_lockup_r(cr, right, (H - brand_symbol_height(4)) / 2, 4, 1, 0);
    if (!f->valid) return;
    const Rgb dim = { 1, 1, 1 };
    if (f->role[0]) {                                           /* .role: margin 18, 22 high, radius 11 */
        right -= 18;
        Text r = rtl(g, 12.5, 600, f->role, 0);
        Rect chip = { right - r.w - 20, (H - 22) / 2, r.w + 20, 22 };
        draw_set_a(cr, dim, .14); draw_rrect_path(cr, chip, 11); cairo_fill(cr);
        put_r(g, &r, right - 10, chip.y + (22 - r.h) / 2, WHITE);
        right = chip.x;
        text_free(&r);
    }
    const char *keys[2] = { "שם", "קונסולה" }, *vals[2] = { f->host, f->url };
    for (int i = 0; i < 2; i++) {                               /* .kv: margin 20, gap 6, 13px */
        if (!vals[i][0] || (i == 1 && g->sm)) continue;         /* .sm hides the address */
        right -= 20;
        Text k = rtl(g, 13, 400, keys[i], 0), v = mono(g, 13, 600, vals[i]);
        cairo_save(cr); cairo_push_group(cr);
        put_r(g, &k, right, (H - k.h) / 2, WHITE);
        cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, .7); cairo_restore(cr);
        right -= k.w + 6;
        put_r(g, &v, right, (H - v.h) / 2, WHITE);
        right -= v.w;
        text_free(&k); text_free(&v);
    }
    double x = pad;                                             /* .end: clock, version, admin; gap 18 */
    Text clock = mono(g, 15, 500, f->clock);
    put_l(g, &clock, x, (H - clock.h) / 2, WHITE);
    x += clock.w + 18;
    text_free(&clock);
    Text vv = mono(g, 12, 400, f->version), vk = rtl(g, 12, 400, "גרסה", 0);
    cairo_push_group(cr);
    put_l(g, &vv, x, (H - vv.h) / 2, WHITE);
    put_l(g, &vk, x + vv.w + 4, (H - vk.h) / 2, WHITE);
    cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, .7);
    x += vv.w + 4 + vk.w + 18;
    text_free(&vv); text_free(&vk);
    if (f->admin[0]) {                                          /* .adm */
        Text a = rtl(g, 12, 600, f->admin, 0);
        Rect chip = { x, (H - 22) / 2, a.w + 20, 22 };
        draw_set_a(cr, (Rgb){ 225 / 255.0, 179 / 255.0, 79 / 255.0 }, .22);
        draw_rrect_path(cr, chip, 11); cairo_fill(cr);
        put_r(g, &a, chip.x + chip.w - 10, chip.y + (22 - a.h) / 2, (Rgb){ 1, 217 / 255.0, 138 / 255.0 });
        text_free(&a);
    }
}

static Rgb dot_color(const G *g, const char *k) {
    if (!strcmp(k, "warn")) return g->t->warn;
    if (!strcmp(k, "bad")) return g->t->danger;
    if (!strcmp(k, "idle")) return g->t->led_idle;
    if (!strcmp(k, "info")) return g->t->info;
    return g->t->led_ok;
}

/* one key item: caps (right), label, lock (left); returns its width */
static double key_item(G *g, double right, double cy, const DcuiKey *k, int draw) {
    double w = kcs(g, right, cy, k->caps, kc_plain(g), draw);
    Text l = rtl(g, 12.5, 400, k->label, 0);
    if (draw) put_r(g, &l, right - w - 6, cy - l.h / 2.0, g->t->muted);
    w += 6 + l.w;
    text_free(&l);
    if (k->lock) {
        if (draw) icon_lock(g, right - w - 6 - 13, cy - 6.5, 13, g->t->muted);
        w += 6 + 13;
    }
    return w;
}

static void status_line(G *g, const DcuiFrame *f, double W, double H) {
    cairo_t *cr = g->cr;
    const double h = 40, y = H - h, cy = y + h / 2, pad = g->sm ? 16 : 24, gap = g->sm ? 10 : 14;
    draw_fill_rrect(cr, (Rect){ 0, y, W, h }, 0, g->t->status_bg);
    hline(g, 0, y, W, g->t->hair);
    if (!f->valid) return;
    double right = W - pad;                                     /* .w: dot, word, rest; gap 8 */
    cairo_arc(cr, right - 4.5, cy, 4.5, 0, 2 * M_PI); draw_set(cr, dot_color(g, f->dot)); cairo_fill(cr);
    right -= 9 + 8;
    Text w = rtl(g, 12.5, 700, f->word, 0);
    put_r(g, &w, right, cy - w.h / 2.0, g->t->ink);
    right -= w.w + 8;
    text_free(&w);
    Text r = rtl(g, 12.5, 400, f->rest, 0);
    put_r(g, &r, right, cy - r.h / 2.0, g->t->muted);
    text_free(&r);
    /* .keys at the left edge; the first item is the rightmost of the group */
    double total = 0, lang_w = 0;
    KcStyle en = { g->t->surface, g->t->indigo, g->t->action_fg, 20, 11.5 };   /* .lang .kc */
    if (f->lang[0]) {
        Text lt = rtl(g, 12.5, 400, f->lang, 0);
        lang_w = kc_w(g, "EN", 11.5) + 8 + lt.w;
        text_free(&lt);
        total += lang_w;
    }
    for (int i = 0; i < f->nkeys; i++) total += (total > 0 ? gap : 0) + key_item(g, 0, cy, &f->keys[i], 0);
    double x = pad + total;
    if (f->lang[0]) {
        double cw = kc(g, x, cy, "EN", en);
        Text lt = rtl(g, 12.5, 400, f->lang, 0);
        put_r(g, &lt, x - cw - 8, cy - lt.h / 2.0, g->t->muted);
        text_free(&lt);
        x -= lang_w + gap;
    }
    for (int i = 0; i < f->nkeys; i++) x -= key_item(g, x, cy, &f->keys[i], 1) + gap;
}

/* ---- rows (.row) ------------------------------------------------------------- */

typedef struct { double x, w; double label_w; double pad_x; } RowBox;

static double row_draw(G *g, const DcuiRow *r, RowBox b, double y, int first, int draw) {
    const Theme *t = g->t;
    const int fp = !strcmp(r->kind, "fprow");
    const int stacked = fp && g->sm;                            /* .sm .row.fpr: the label on its own line */
    const double pad_y = g->sm ? 5 : 7, right = b.x + b.w;
    double pw = r->pill[0] ? pill_w(g, r->pill) : 0;
    double vx = b.x + (pw > 0 ? pw + 12 : 0);                   /* the value cell's left edge */
    double vright = stacked ? right : right - b.label_w - 12;
    double vw = vright - vx;
    if (vw < 40) vw = 40;
    /* label */
    Text lab = r->label_style[0] ? mono(g, 12.5, 600, r->label) : rtl(g, 13.5, 400, r->label, stacked ? 0 : b.label_w);
    Text sl = { 0 };
    if (fp) {
        if (stacked) {
            char both[160]; snprintf(both, sizeof both, "%s %s", r->label, r->sublabel);
            text_free(&lab); lab = rtl(g, 13.5, 400, both, 0);
        } else sl = rtl(g, 12, 400, r->sublabel, b.label_w);
    }
    double lab_h = lab.h + (sl.layout ? sl.h : 0);
    /* value */
    Text v1 = { 0 }, v2 = { 0 };
    double val_h = 0;
    if (fp) {
        double px = g->sm ? 11 : 12.5;
        v1 = mk(g, FONT_MONO, px, 500, r->value, 0, 1, 0);
        v2 = mk(g, FONT_MONO, px, 500, r->value2, 0, 1, 0);
        val_h = v1.h + v2.h;
    } else if (!strcmp(r->value_style, "strong-mono")) {
        v1 = mk(g, FONT_MONO, 15, 600, r->value, 0, 1, 0); val_h = v1.h;
    } else if (!strcmp(r->value_style, "mono")) {
        v1 = mk(g, FONT_MONO, 13.5, 600, r->value, 0, 1, 0); val_h = v1.h;
    } else {
        v1 = rtl(g, 14, 400, r->value, vw); val_h = v1.h;
    }
    Text sub = { 0 };
    int tone_bad = !strcmp(r->tone, "bad"), tone_warn = !strcmp(r->tone, "warn");
    if (r->sub[0]) {
        sub = (tone_bad || tone_warn) ? rtl(g, 12.5, 600, r->sub, vw) : rtl(g, 12.5, 400, r->sub, vw);
        val_h += 1 + sub.h;
    }
    double body = stacked ? lab.h + 2 + fmax(val_h, 24) : fmax(fmax(lab_h, val_h), r->pill[0] ? 24 : 0);
    double h = pad_y + body + pad_y;
    if (draw) {
        if (tone_bad || tone_warn)
            draw_fill_rrect(g->cr, (Rect){ b.x - b.pad_x, y, b.w + 2 * b.pad_x, h }, 0,
                            tone_bad ? t->danger_soft : t->warning_soft);
        else if (!first) hline(g, b.x, y, b.w, t->hair);
        double top = y + pad_y, cy = top + body / 2;
        if (stacked) {
            put_r(g, &lab, right, top, t->muted);
            top += lab.h + 2; cy = top + fmax(val_h, 24) / 2;
        } else {
            double ly = top + (body - lab_h) / 2;
            put_r(g, &lab, right, ly, r->label_style[0] ? t->ink : t->muted);
            if (sl.layout) put_r(g, &sl, right, ly + lab.h, t->muted);
        }
        double vy = cy - val_h / 2;
        if (fp) {                                               /* .fp: LTR, text-align:left in the cell */
            put_l(g, &v1, vx, vy, t->ink); put_l(g, &v2, vx, vy + v1.h, t->ink);
            vy += v1.h + v2.h;
        } else {
            put_r(g, &v1, vright, vy, t->ink); vy += v1.h;
        }
        if (sub.layout) put_r(g, &sub, vright, vy + 1, tone_bad ? t->danger : tone_warn ? t->warn : t->muted);
        if (r->pill[0]) pill(g, b.x, cy, r->pill_kind, r->pill);
    }
    text_free(&lab); text_free(&sl); text_free(&v1); text_free(&v2); text_free(&sub);
    return h;
}

/* ---- main: hero + tiles ------------------------------------------------------ */

static double problem_draw(G *g, const DcuiProb *p, double x, double w, double y, int draw) {
    const Theme *t = g->t;
    const double px = g->sm ? 12 : 14, py = 9;
    const int warn = !strcmp(p->sev, "warn");
    double kw = 0;
    if (p->cap[0]) {
        Text kl = rtl(g, 12.5, 600, p->cap_label, 0);
        kw = kc_w(g, p->cap, 11.5) + 6 + kl.w;
        text_free(&kl);
    }
    double tw = w - 4 - px - 28 - 12 - px - (kw > 0 ? kw + 12 : 0);
    Text ti = rtl(g, 14.5, 700, p->title, tw), wh = rtl(g, 13, 400, p->what, tw);
    Text td = rtlm_ink(g, 13, 400, p->todo_m, tw);
    double h = py + fmax(26, ti.h + 1 + wh.h + 1 + td.h) + py;
    if (draw) {
        Rgb edge = warn ? t->warn : t->danger;
        draw_fill_rrect(g->cr, (Rect){ x, y, w, h }, 6, t->surface);
        cairo_save(g->cr);                                      /* border-inline-start: 4px, the right edge */
        draw_rrect_path(g->cr, (Rect){ x, y, w, h }, 6); cairo_clip(g->cr);
        cairo_rectangle(g->cr, x + w - 4, y, 4, h); draw_set(g->cr, edge); cairo_fill(g->cr);
        cairo_restore(g->cr);
        double right = x + w - 4 - px, ty = y + py;
        cairo_arc(g->cr, right - 12, ty + 13, 12, 0, 2 * M_PI); draw_set(g->cr, edge); cairo_fill(g->cr);
        Text n = rtl(g, 14, 700, p->n, 0);
        text_draw(g->cr, &n, right - 12 - n.w / 2.0, ty + 13 - n.h / 2.0, on_fill(g));
        text_free(&n);
        right -= 28 + 12;
        put_r(g, &ti, right, ty, t->ink); ty += ti.h + 1;
        put_r(g, &wh, right, ty, t->muted); ty += wh.h + 1;
        put_r(g, &td, right, ty, t->ink);
        if (kw > 0) {                                           /* .is .k: centred, action colour */
            double cy = y + h / 2, kx = x + px + kw;
            double cw = kc(g, kx, cy, p->cap, kc_plain(g));
            Text kl = rtl(g, 12.5, 600, p->cap_label, 0);
            put_r(g, &kl, kx - cw - 6, cy - kl.h / 2.0, t->action_fg);
            text_free(&kl);
        }
    }
    text_free(&ti); text_free(&wh); text_free(&td);
    return h;
}

static double hero(G *g, const DcuiFrame *f, double x, double y, double w, int draw) {
    const Theme *t = g->t;
    const int bad = !strcmp(f->hero_kind, "bad");
    const double ppy = bad ? 16 : (g->sm ? 16 : 20), ppx = bad ? 22 : (g->sm ? 18 : 24);
    const double bw = bad ? 2 : 1;
    double inner = w - 2 * ppx - 2 * bw;
    Text ml = { 0 }, mv = { 0 };
    double meta_w = 0, meta_h = 0;
    if (f->meta_label[0]) {
        ml = rtl(g, 12, 400, f->meta_label, 0); mv = mono(g, 15, 600, f->meta_value);
        meta_w = fmax(ml.w, mv.w); meta_h = 4 + ml.h + 4 + mv.h;
    }
    double tw = inner - 56 - 20 - (meta_w > 0 ? meta_w + 20 : 0);
    Text h1 = rtl(g, bad ? (g->sm ? 22 : 24) : (g->sm ? 22 : 26), 600, f->hero_title, tw);
    Text sub = rtlm_ink(g, 14, 400, f->hero_sub_m, tw);
    double text_h = h1.h + 4 + sub.h, probs_h = 0;
    double pw = inner - 56 - 20;                                /* .iss sits in the text column */
    if (bad && f->nprobs) {
        probs_h = g->sm ? 10 : 12;
        for (int i = 0; i < f->nprobs; i++)
            probs_h += (i ? (g->sm ? 8 : 10) : 0) + problem_draw(g, &f->probs[i], 0, pw, 0, 0);
    }
    double body = fmax(56, fmax(text_h + probs_h, meta_h));
    double h = bw + ppy + body + ppy + bw;
    if (draw) {
        Rect box = { x, y, w, h };
        draw_fill_rrect(g->cr, box, 8, bad ? t->danger : t->hair);
        draw_fill_rrect(g->cr, (Rect){ x + bw, y + bw, w - 2 * bw, h - 2 * bw }, 8 - bw, bad ? t->danger_soft : t->surface);
        double right = x + w - bw - ppx, top = y + bw + ppy;
        hero_glyph(g, right - 56, top, f->hero_kind);
        right -= 56 + 20;
        put_r(g, &h1, right, top, t->ink);
        put_r(g, &sub, right, top + h1.h + 4, t->muted);
        double py = top + text_h + (g->sm ? 10 : 12);
        for (int i = 0; i < f->nprobs && bad; i++)
            py += problem_draw(g, &f->probs[i], right - pw, pw, py, 1) + (g->sm ? 8 : 10);
        if (meta_w > 0) {                                       /* .meta: column, aligned to the left */
            double mx = x + bw + ppx;
            put_l(g, &ml, mx, top + 4, t->muted);
            put_l(g, &mv, mx, top + 4 + ml.h + 4, t->ink);
        }
    }
    text_free(&ml); text_free(&mv); text_free(&h1); text_free(&sub);
    return h;
}

static void tile(G *g, const DcuiTile *tl, Rect r) {
    const Theme *t = g->t;
    draw_fill_rrect(g->cr, r, 8, t->hair);
    draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 7, t->surface);
    cairo_save(g->cr);
    draw_rrect_path(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 7); cairo_clip(g->cr);
    const double px = 18;
    double x = r.x + 1 + px, w = r.w - 2 - 2 * px, y = r.y + 1 + 14;
    Text h2 = rtl(g, 13, 600, tl->title, 0);
    double hh = fmax(h2.h, tl->pill[0] ? 24 : 0);
    put_r(g, &h2, x + w, y + (hh - h2.h) / 2, t->muted);
    if (tl->pill[0]) pill(g, x, y + hh / 2, tl->pill_kind, tl->pill);
    text_free(&h2);
    y += hh + 4;
    RowBox b = { x, w, g->sm ? 118 : 150, px };
    for (int i = 0; i < tl->nrows; i++) y += row_draw(g, &tl->rows[i], b, y, i == 0, 1);
    cairo_restore(g->cr);
}

static void main_body(G *g, const DcuiFrame *f, Rect page) {
    const double gap = g->sm ? 12 : 16;
    double y = page.y;
    if (f->hero_kind[0]) y += hero(g, f, page.x, y, page.w, 1) + gap;
    double th = page.y + page.h - y, tw = (page.w - gap) / 2, rh = (th - gap) / 2;
    for (int i = 0; i < f->ntiles && i < 4; i++) {              /* RTL grid: 0 top-right, 1 top-left ... */
        double tx = (i % 2 == 0) ? page.x + page.w - tw : page.x;
        double ty = y + (i / 2) * (rh + gap);
        tile(g, &f->tiles[i], (Rect){ tx, ty, tw, f->ntiles > 2 ? rh : th });
    }
}

/* ---- settings (.ttl, .cz) ---------------------------------------------------- */

static void settings_body(G *g, const DcuiFrame *f, Rect page) {
    const Theme *t = g->t;
    double right = page.x + page.w, y = page.y;
    Text h1 = rtl(g, 24, 600, f->ttl, 0), p = rtl(g, 13, 400, f->ttl_sub, 0);
    put_r(g, &h1, right, y, t->ink); y += h1.h + 2;
    put_r(g, &p, right, y, t->muted); y += p.h + (g->sm ? 12 : 16);
    text_free(&h1); text_free(&p);
    double lw = g->sm ? 380 : 420, gap = 20, bottom = page.y + page.h;
    double ly = y;
    for (int i = 0; i < f->nitems; i++) {                       /* .it */
        const DcuiItem *it = &f->items[i];
        Text ti = rtl(g, 15, 600, it->title, 0), su = rtl(g, 12.5, 400, it->sub, 0);
        double h = 12 + fmax(38, ti.h + su.h) + 12 + 2;
        Rect r = { right - lw, ly, lw, h };
        draw_fill_rrect(g->cr, r, 8, it->selected ? t->indigo : t->hair);
        double bw = it->selected ? 2 : 1;
        draw_fill_rrect(g->cr, (Rect){ r.x + bw, r.y + bw, r.w - 2 * bw, r.h - 2 * bw }, 8 - bw,
                        it->selected ? t->indigo_soft : t->surface);
        double ir = right - 17, cy = ly + h / 2;
        Rect ic = { ir - 38, cy - 19, 38, 38 };
        draw_fill_rrect(g->cr, ic, 8, it->selected ? t->indigo : t->indigo_soft);
        icon_menu(g, (Rect){ ic.x + 9, ic.y + 9, 20, 20 }, it->icon, it->selected ? WHITE : t->action_fg);
        double tx = ic.x - 14, ty = cy - (ti.h + su.h) / 2;
        put_r(g, &ti, tx, ty, t->ink); put_r(g, &su, tx, ty + ti.h, t->muted);
        Text ch = rtl(g, 18, 400, "‹", 0);
        put_l(g, &ch, r.x + 17, cy - ch.h / 2.0, it->selected ? t->action_fg : t->muted);
        text_free(&ti); text_free(&su); text_free(&ch);
        ly += h + 8;
    }
    if (f->na_m[0]) {                                           /* .na: dashed */
        ly += 6;
        Text na = rtlm_ink(g, 12.5, 400, f->na_m, lw - 28);
        Rect r = { right - lw, ly, lw, na.h + 20 };
        cairo_save(g->cr);
        double dash[] = { 4, 3 };
        cairo_set_dash(g->cr, dash, 2, 0); cairo_set_line_width(g->cr, 1);
        draw_rrect_path(g->cr, (Rect){ r.x + .5, r.y + .5, r.w - 1, r.h - 1 }, 8);
        draw_set(g->cr, t->hair); cairo_stroke(g->cr);
        cairo_restore(g->cr);
        put_r(g, &na, right - 14, ly + 10, t->muted);
        text_free(&na);
    }
    /* .det: the explanation pane */
    Rect d = { page.x, y, page.w - lw - gap, bottom - y };
    draw_fill_rrect(g->cr, d, 8, t->hair);
    draw_fill_rrect(g->cr, (Rect){ d.x + 1, d.y + 1, d.w - 2, d.h - 2 }, 7, t->surface);
    double dx = d.x + 1 + 24, dw = d.w - 2 - 48, dr = dx + dw, dy = d.y + 1 + 22;
    Text h2 = rtl(g, 20, 600, f->pane_title, dw), desc = rtlm_ink(g, 14, 400, f->pane_desc_m, dw);
    put_r(g, &h2, dr, dy, t->ink); dy += h2.h + 14;
    put_r(g, &desc, dr, dy, t->ink); dy += desc.h + 14;
    text_free(&h2); text_free(&desc);
    if (f->nprows) {                                            /* .cur */
        hline(g, dx, dy, dw, t->hair); dy += 12;
        RowBox b = { dx, dw, g->sm ? 118 : 150, 24 };
        for (int i = 0; i < f->nprows; i++) dy += row_draw(g, &f->prows[i], b, dy, i == 0, 1);
    }
    if (f->npnotes) {                                           /* .note at the bottom (.grow above) */
        Text n[DCUI_MAX_NOTES];
        double nh = 20;
        for (int i = 0; i < f->npnotes; i++) { n[i] = rtlm_ink(g, 12.5, 400, f->pnotes[i], dw - 24); nh += n[i].h + (i ? 3 : 0); }
        double ny = d.y + d.h - 1 - 22 - nh;
        if (ny < dy + 14) ny = dy + 14;
        draw_fill_rrect(g->cr, (Rect){ dx, ny, dw, nh }, 6, t->porcelain);
        ny += 10;
        for (int i = 0; i < f->npnotes; i++) { put_r(g, &n[i], dr - 12, ny, t->muted); ny += n[i].h + 3; text_free(&n[i]); }
    }
}

/* ---- the dialog (.scrim .dlg) ------------------------------------------------- */

typedef struct { G *g; const DcuiFrame *f; double x, w; int danger; } Dlg;

static double btn_row(Dlg *d, int from, int to, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    double right = d->x + d->w;
    for (int i = from; i < to; i++) {
        const DcuiEl *e = &d->f->el[i];
        const int pri = !strcmp(e->d, "pri"), dng = !strcmp(e->d, "danger");
        Text l = rtl(g, 14, 500, e->a, 0);
        double kw = e->c[0] ? kc_w(g, e->c, 11) : 0;
        double w = 20 + l.w + (kw > 0 ? 10 + kw : 0) + 20;
        Rect r = { right - w, y, w, 40 };
        if (draw) {
            Rgb bg = pri ? t->indigo : dng ? t->danger : t->surface, line = pri ? t->indigo : dng ? t->danger : t->hair;
            Rgb fg = pri ? t->on_ink : dng ? on_fill(g) : t->action_fg;
            if (e->f1) {                                        /* .btn.foc: 2px outline, 2px offset */
                draw_border_rrect(g->cr, (Rect){ r.x - 4, r.y - 4, r.w + 8, r.h + 8 }, 7, dng ? t->danger : t->indigo, 2);
            }
            draw_fill_rrect(g->cr, r, 4, line);
            draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 3, bg);
            put_r(g, &l, r.x + r.w - 20, y + (40 - l.h) / 2, fg);
            if (kw > 0) {
                KcStyle ks = kc_plain(g);
                ks.h = 18; ks.px = 11;
                if (pri || dng) { ks.bg = fg; ks.line = fg; ks.fg = fg; }
                if (pri || dng) {                               /* rgb(255 255 255 / 18%) on the fill, 40% border */
                    double cw = kc_w(g, e->c, 11), cx = r.x + 20, cy = y + 20;
                    cairo_save(g->cr);
                    draw_set_a(g->cr, WHITE, .40); draw_rrect_path(g->cr, (Rect){ cx, cy - 9, cw, 18 }, 4); cairo_fill(g->cr);
                    draw_fill_rrect(g->cr, (Rect){ cx + 1, cy - 8, cw - 2, 15 }, 3, bg);
                    draw_set_a(g->cr, WHITE, .18); draw_rrect_path(g->cr, (Rect){ cx + 1, cy - 8, cw - 2, 15 }, 3); cairo_fill(g->cr);
                    cairo_restore(g->cr);
                    Text c = mono(g, 11, 600, e->c);
                    text_draw(g->cr, &c, cx + (cw - c.w) / 2, cy - 9 + (17 - c.h) / 2, fg);
                    text_free(&c);
                } else kc(g, r.x + 20 + kw, y + 20, e->c, ks);
            }
        }
        right -= w + 10;
        text_free(&l);
    }
    return 40;
}

static double field_el(Dlg *d, const DcuiEl *e, double fx, double fw, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    Text lab = rtl(g, 12.5, 600, e->b, fw);
    double h = lab.h + 6 + 40;
    if (draw) {
        put_r(g, &lab, fx + fw, y, t->ink);
        Rect r = { fx, y + lab.h + 6, fw, 40 };
        const int ro = !strcmp(e->c, "ro") || !strcmp(e->c, "unused"), pw = !strncmp(e->c, "password", 8);
        if (e->f1) draw_border_rrect(g->cr, (Rect){ r.x - 3, r.y - 3, r.w + 6, r.h + 6 }, 5, t->indigo, 2);
        draw_fill_rrect(g->cr, r, 3, e->f1 ? (ro ? t->porcelain : t->field) : t->hair);
        draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 2, ro ? t->porcelain : t->field);
        double x = r.x + 12, cy = r.y + 20;
        if (pw) {                                               /* the dots, never the password */
            int n = atoi(e->a); if (n > 64) n = 64; if (n < 0) n = 0;
            char dots[64 * 3 + 1] = ""; for (int i = 0; i < n; i++) strcat(dots, "\xe2\x80\xa2");
            char mk_[512]; snprintf(mk_, sizeof mk_, "<span letter_spacing=\"3072\">%s</span>", dots);
            Text dt = mk(g, FONT_MONO, 18, 500, mk_, 0, 1, 1);
            if (n) { put_l(g, &dt, x, cy - dt.h / 2.0, t->ink); x += dt.w + 8; }
            text_free(&dt);
        } else if (e->a[0]) {
            Text v = mk(g, FONT_MONO, 15, 500, e->a, 0, 1, 0);
            put_l(g, &v, x, cy - v.h / 2.0, ro ? t->muted : t->ink); x += v.w + 2;
            text_free(&v);
        }
        if (e->f1) { cairo_rectangle(g->cr, x, cy - 10, 2, 20); draw_set(g->cr, t->indigo); cairo_fill(g->cr); }
        if (e->f2) {                                            /* .inp .en: the chip at the field's end */
            Text en = mono(g, 11, 700, "EN");
            Rect c = { r.x + r.w - 12 - en.w - 14, cy - 11, en.w + 14, 22 };
            draw_fill_rrect(g->cr, c, 4, t->indigo);
            draw_fill_rrect(g->cr, (Rect){ c.x + 1, c.y + 1, c.w - 2, c.h - 2 }, 3, t->indigo_soft);
            text_draw(g->cr, &en, c.x + 7, cy - en.h / 2.0, t->action_fg);
            text_free(&en);
        }
    }
    text_free(&lab);
    return h;
}

static double opts_row(Dlg *d, int from, int to, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    int n = to - from; if (n < 1) return 0;
    double gap = 12, w = (d->w - gap * (n - 1)) / n, h = 0;
    Text ti[4], su[4];
    for (int i = 0; i < n && i < 4; i++) {
        const DcuiEl *e = &d->f->el[from + i];
        ti[i] = rtl(g, 15, 700, e->a, w - 32 - 30);
        su[i] = rtl(g, 12.5, 400, e->b, w - 32 - 30);
        h = fmax(h, 14 + ti[i].h + 2 + su[i].h + 14);
    }
    for (int i = 0; i < n && i < 4; i++) {
        const DcuiEl *e = &d->f->el[from + i];
        double x = d->x + d->w - (i + 1) * w - i * gap;
        if (draw) {
            Rgb sel = d->danger ? t->danger : t->indigo, soft = d->danger ? t->danger_soft : t->indigo_soft;
            Rect r = { x, y, w, h };
            if (e->f1 && e->f2) draw_border_rrect(g->cr, (Rect){ r.x - 4, r.y - 4, r.w + 8, r.h + 8 }, 11, t->indigo, 2);
            draw_fill_rrect(g->cr, r, 8, e->f1 ? sel : t->hair);
            double bw = e->f1 ? 2 : 1;
            draw_fill_rrect(g->cr, (Rect){ r.x + bw, r.y + bw, r.w - 2 * bw, r.h - 2 * bw }, 8 - bw, e->f1 ? soft : t->surface);
            double right = x + w - 16, ty = y + 14;
            cairo_arc(g->cr, right - 9, ty + 11, 8, 0, 2 * M_PI);          /* .rd: 18px, 2px border */
            cairo_set_line_width(g->cr, 2); draw_set(g->cr, e->f1 ? sel : t->hair); cairo_stroke(g->cr);
            if (e->f1) { cairo_arc(g->cr, right - 9, ty + 11, 5, 0, 2 * M_PI); draw_set(g->cr, sel); cairo_fill(g->cr); }
            put_r(g, &ti[i], right - 18 - 12, ty, t->ink);
            put_r(g, &su[i], right - 18 - 12, ty + ti[i].h + 2, t->muted);
        }
        text_free(&ti[i]); text_free(&su[i]);
    }
    return h;
}

static double radio_el(Dlg *d, const DcuiEl *e, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    Text ti = mk(g, FONT_MONO, 13.5, 600, e->a, 0, 1, 0), su = rtl(g, 12, 400, e->b, d->w - 60);
    double h = 8 + ti.h + 1 + su.h + 8;
    if (draw) {
        Rect r = { d->x, y, d->w, h };
        if (e->f1 && e->f2) draw_border_rrect(g->cr, (Rect){ r.x - 4, r.y - 4, r.w + 8, r.h + 8 }, 9, t->indigo, 2);
        draw_fill_rrect(g->cr, r, 6, e->f1 ? t->indigo : t->hair);
        draw_fill_rrect(g->cr, (Rect){ r.x + 1, r.y + 1, r.w - 2, r.h - 2 }, 5, e->f1 ? t->indigo_soft : t->surface);
        double right = r.x + r.w - 12;
        cairo_arc(g->cr, right - 8, y + h / 2, 7, 0, 2 * M_PI);
        cairo_set_line_width(g->cr, 2); draw_set(g->cr, e->f1 ? t->indigo : t->hair); cairo_stroke(g->cr);
        if (e->f1) { cairo_arc(g->cr, right - 8, y + h / 2, 4, 0, 2 * M_PI); draw_set(g->cr, t->indigo); cairo_fill(g->cr); }
        put_r(g, &ti, right - 16 - 10, y + 8, t->ink);
        put_r(g, &su, right - 16 - 10, y + 8 + ti.h + 1, t->muted);
    }
    text_free(&ti); text_free(&su);
    return h;
}

static double cons_el(Dlg *d, const DcuiEl *e, double y, int draw) {
    G *g = d->g; const Theme *t = g->t;
    const int hot = e->type == DEL_ALERT;
    double pad = hot ? 12 : 0, w = d->w - 2 * pad - 20 - 10;
    Text tx = rtlm(g, 13.5, 400, e->a, w);
    double h = (hot ? 10 : 0) + fmax(22, tx.h) + (hot ? 10 : 0);
    if (draw) {
        if (hot) {
            draw_fill_rrect(g->cr, (Rect){ d->x, y, d->w, h }, 6, t->danger);
            draw_fill_rrect(g->cr, (Rect){ d->x + 1, y + 1, d->w - 2, h - 2 }, 5, t->danger_soft);
        }
        double right = d->x + d->w - pad, top = y + (hot ? 10 : 0);
        cairo_arc(g->cr, right - 10, top + 11, 10, 0, 2 * M_PI);
        draw_set(g->cr, hot ? t->danger : t->hover); cairo_fill(g->cr);
        Text b = rtl(g, 12, 700, hot ? "!" : "\xe2\x80\xa2", 0);
        text_draw(g->cr, &b, right - 10 - b.w / 2.0, top + 11 - b.h / 2.0, hot ? on_fill(g) : t->muted);
        text_free(&b);
        put_r(g, &tx, right - 20 - 10, top, t->ink);
    }
    text_free(&tx);
    return h;
}

static double dialog_body(Dlg *d, double y0, int draw) {
    G *g = d->g; const Theme *t = g->t; const DcuiFrame *f = d->f;
    double y = y0, right = d->x + d->w;
    Text h2 = rtl(g, 22, 600, f->dlg_title, d->w), lead = rtl(g, 14, 400, f->dlg_lead, d->w);
    if (draw) { put_r(g, &h2, right, y, t->ink); put_r(g, &lead, right, y + h2.h + 6, t->muted); }
    y += h2.h + 6 + lead.h;
    text_free(&h2); text_free(&lead);
    int prev = 0;
    for (int i = 0; i < f->nel; ) {
        const DcuiEl *e = &f->el[i];
        int j = i + 1;
        switch (e->type) {
        case DEL_FIELD: {
            /* a run of three or more plain text fields (the network form's
             * address, prefix, gateway, DNS) goes two to a row, so the form
             * fits a 768px screen; the first of a pair is the right one */
            int run = 0;
            while (i + run < f->nel && f->el[i + run].type == DEL_FIELD
                   && (!strcmp(f->el[i + run].c, "text") || !strcmp(f->el[i + run].c, "unused"))) run++;
            if (run >= 3) {
                double hw = (d->w - 16) / 2;
                for (int m = 0; m < run; m += 2) {
                    y += m ? 12 : 18;
                    double h1 = field_el(d, &f->el[i + m], d->x + d->w - hw, hw, y, draw), h2 = 0;
                    if (m + 1 < run) h2 = field_el(d, &f->el[i + m + 1], d->x, hw, y, draw);
                    y += fmax(h1, h2);
                }
                j = i + run;
            } else {
                y += 18; y += field_el(d, e, d->x, d->w, y, draw);
            }
            break;
        }
        case DEL_HINT: {
            y += 8;
            double cw = kc_w(g, e->c, 11.5);
            Text r = rtl(g, 12.5, 400, e->b, 0);
            Text tx = rtlm_ink(g, 12.5, 400, e->a, d->w - cw - 8 - r.w - 16);
            double h = fmax(20, tx.h);
            if (draw) {
                kc(g, right, y + h / 2, e->c, kc_plain(g));
                put_r(g, &tx, right - cw - 8, y + (h - tx.h) / 2, t->muted);
                put_l(g, &r, d->x, y + (h - r.h) / 2, t->muted);
            }
            y += h; text_free(&r); text_free(&tx);
            break;
        }
        case DEL_NOTE: {
            while (j < f->nel && f->el[j].type == DEL_NOTE) j++;
            y += 16;
            double h = 20; Text n[8]; int k = 0;
            for (int m = i; m < j && k < 8; m++, k++) { n[k] = rtlm_ink(g, 12.5, 400, f->el[m].a, d->w - 24); h += n[k].h + (k ? 3 : 0); }
            if (draw) {
                draw_fill_rrect(g->cr, (Rect){ d->x, y, d->w, h }, 6, t->porcelain);
                double ny = y + 10;
                for (int m = 0; m < k; m++) { put_r(g, &n[m], right - 12, ny, t->muted); ny += n[m].h + 3; }
            }
            for (int m = 0; m < k; m++) text_free(&n[m]);
            y += h;
            break;
        }
        case DEL_OPT:
            while (j < f->nel && f->el[j].type == DEL_OPT) j++;
            y += 18; y += opts_row(d, i, j, y, draw);
            break;
        case DEL_RADIO: y += prev == DEL_RADIO ? 6 : 14; y += radio_el(d, e, y, draw); break;
        case DEL_ALERT: case DEL_BULLET:
            y += (prev == DEL_ALERT || prev == DEL_BULLET) ? 8 : 16;
            y += cons_el(d, e, y, draw);
            break;
        case DEL_KV: {
            y += prev == DEL_KV && !e->b[0] ? 2 : 10;
            double lw = 170;
            Text l = rtl(g, 12.5, 400, e->b, lw);
            Text v = e->f1 ? mk(g, FONT_MONO, 13, 500, e->a, d->w - lw - 12, 1, 0) : rtl(g, 13.5, 400, e->a, d->w - lw - 12);
            if (draw) {
                put_r(g, &l, right, y, t->muted);
                if (e->f1) put_l(g, &v, d->x, y, t->ink); else put_r(g, &v, right - lw - 12, y, t->ink);
            }
            y += fmax(l.h, v.h); text_free(&l); text_free(&v);
            break;
        }
        case DEL_ERROR: {
            y += 10;
            Text er = rtl(g, 13, 600, e->a, d->w);
            if (draw) put_r(g, &er, right, y, t->danger_fg);
            y += er.h; text_free(&er);
            break;
        }
        case DEL_P: {
            y += 12;
            Text p = rtlm_ink(g, 14, 400, e->a, d->w);
            if (draw) put_r(g, &p, right, y, t->ink);
            y += p.h; text_free(&p);
            break;
        }
        case DEL_BTN:
            while (j < f->nel && f->el[j].type == DEL_BTN) j++;
            y += 22; y += btn_row(d, i, j, y, draw);
            break;
        }
        prev = e->type;
        i = j;
    }
    return y - y0;
}

static void dialog(G *g, const DcuiFrame *f, double W, double H) {
    const Theme *t = g->t;
    cairo_rectangle(g->cr, 0, 48, W, H - 48 - 40);                       /* .scrim */
    draw_set_a(g->cr, (Rgb){ 10 / 255.0, 16 / 255.0, 22 / 255.0 }, .55); cairo_fill(g->cr);
    const int danger = !strcmp(f->dlg_kind, "danger");
    double w = f->dlg_width > 0 ? f->dlg_width : 560;
    if (w > W - 40) w = W - 40;
    Dlg d = { g, f, 0, w - 60, danger };
    double top_border = danger ? 4 : 0;
    double h = top_border + 28 + dialog_body(&d, 0, 0) + 24;
    double x = (W - w) / 2, y = 48 + (H - 48 - 40 - h) / 2;
    if (y < 56) y = 56;
    Rect box = { x, y, w, h };
    draw_box_shadow(g->cr, box, 10, 24, 60, -12, (Rgb){ 0, 0, 0 }, g->dark ? .5 : .18);
    draw_fill_rrect(g->cr, box, 10, t->hair);
    draw_fill_rrect(g->cr, (Rect){ x + 1, y + 1, w - 2, h - 2 }, 9, t->surface);
    if (danger) {                                                         /* .dlg.pw: border-top 4px */
        cairo_save(g->cr);
        draw_rrect_path(g->cr, box, 10); cairo_clip(g->cr);
        cairo_rectangle(g->cr, x, y, w, 4); draw_set(g->cr, t->danger); cairo_fill(g->cr);
        cairo_restore(g->cr);
    }
    d.x = x + 30;
    dialog_body(&d, y + top_border + 28, 1);
}

static void notice(G *g, const DcuiFrame *f, double W, double H) {
    const Theme *t = g->t;
    Text n = rtl(g, 13.5, 500, f->notice, fmin(720, W - 120));
    double w = n.w + 32, h = n.h + 18, x = (W - w) / 2, y = H - 40 - 16 - h;
    draw_box_shadow(g->cr, (Rect){ x, y, w, h }, 8, 6, 16, 0, (Rgb){ 0, 0, 0 }, g->dark ? .4 : .14);
    draw_fill_rrect(g->cr, (Rect){ x, y, w, h }, 8, t->info);
    draw_fill_rrect(g->cr, (Rect){ x + 1, y + 1, w - 2, h - 2 }, 7, t->info_soft);
    put_r(g, &n, x + w - 16, y + 9, t->ink);
    text_free(&n);
}

/* clamp(H/800, 1, 1.4) to a quarter, as screens.c ui_scale: a 1080p screen is
 * the same layout at 1.25, not 1024x768 with more empty space. */
static double scale_for(int H) { double s = floor(H / 800.0 * 4) / 4; return s < 1 ? 1 : s > 1.4 ? 1.4 : s; }

void dcui_draw(const DcuiFrame *f, const Theme *t, cairo_t *cr, int Wpx, int Hpx) {
    double s = scale_for(Hpx), W = Wpx / s, H = Hpx / s;
    G g = { cr, t, W < 1200, t == &THEME_DARK };
    cairo_save(cr);
    cairo_scale(cr, s, s);
    draw_fill_rrect(cr, (Rect){ 0, 0, W, H }, 0, t->porcelain);
    header(&g, f, W);
    if (f->valid) {
        const double py = g.sm ? 18 : 24, px = g.sm ? 20 : 32;
        Rect page = { px, 48 + py, W - 2 * px, H - 48 - 40 - 2 * py };
        cairo_save(cr);
        cairo_rectangle(cr, 0, 48, W, H - 88); cairo_clip(cr);
        if (!strcmp(f->screen, "settings")) settings_body(&g, f, page);
        else main_body(&g, f, page);
        cairo_restore(cr);
        if (f->dialog) dialog(&g, f, W, H);
        if (f->notice[0]) notice(&g, f, W, H);
    }
    status_line(&g, f, W, H);
    cairo_restore(cr);
}
