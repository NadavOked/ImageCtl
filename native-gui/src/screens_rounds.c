/* #st-room and #st-class -- the two cards whose bodies room.js and
 * classes.js build at runtime. The text is theirs; the numbers are
 * station.css (.room-machines / .room-row / .cls-bars / .menu-card). The
 * data comes from the --state file; the setup form (image, target) and the
 * confirm box are the operator's. The classroom wizard's steps 2 and 3
 * (machine grid, image list) are not built -- see README. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "screens_rounds.h"

/* ---- .room-machines rows (room.js machineRows / classes.js renderLive) ---- */

static void hex_of(Rgb c, char *out, size_t n) {
    snprintf(out, n, "#%02X%02X%02X", (int)(c.r * 255 + 0.5), (int)(c.g * 255 + 0.5), (int)(c.b * 255 + 0.5));
}

int smart_level(const char *smart) {
    if (!strcmp(smart, "failed_last")) return 2;
    if (!strcmp(smart, "fail") || !strcmp(smart, "warn")) return 1;
    return 0;
}

Rgb smart_color(const Theme *t, const char *smart) {
    int lvl = smart_level(smart);
    return lvl == 2 ? t->danger : lvl == 1 ? t->warn : t->led_ok;
}

const char *smart_he(const char *smart) {
    if (!smart || !smart[0] || !strcmp(smart, "unchecked")) return "לא נבדק";
    if (!strcmp(smart, "pending")) return "ממתין להכרעה";
    if (!strcmp(smart, "passed")) return "תקין";
    if (!strcmp(smart, "failed_last")) return "נכשל בשיכפול הקודם";
    if (!strcmp(smart, "fail")) return "נכשל";
    if (!strcmp(smart, "warn")) return "אזהרה";
    return smart;
}

const IdleDisk *idle_for_port(const State *s, int port) {
    for (int i = 0; i < s->nidisks; i++)
        if (s->idisks[i].port == port) return &s->idisks[i];
    return NULL;
}

/* The status span, as Pango markup (room-ok / room-bad / room-warn / sub). */
void room_status_markup(const App *a, const Machine *m, int mode, char *out, size_t n) {
    char ok[8], bad[8], warn[8], e[640], lbl[96];   /* e: 120 bytes escaped, worst case x5 */
    hex_of(a->theme->led_ok, ok, sizeof ok);
    hex_of(a->theme->danger, bad, sizeof bad);
    hex_of(a->theme->warn, warn, sizeof warn);
    text_escape(m->error, e, sizeof e);
    progress_label(lbl, sizeof lbl, m->pct, m->moving, 0);
    if (mode == ROWS_CLASS) {
        if (!strcmp(m->state, "failed"))       snprintf(out, n, "<span foreground=\"%s\">נכשל · %s</span>", bad, e);
        else if (!strcmp(m->state, "done"))    snprintf(out, n, "<span foreground=\"%s\">הסתיים</span>", ok);
        else if (!strcmp(m->state, "waiting")) snprintf(out, n, "ממתין לשידור");
        else                                   snprintf(out, n, "%s", lbl);
        return;
    }
    if (mode == ROWS_ROOM_LIVE && m->joined) {
        /* three endings, not two (#67) */
        if (!strcmp(m->state, "failed"))        snprintf(out, n, "<span foreground=\"%s\">נכשל · %s</span>", bad, e);
        else if (!strcmp(m->state, "partial"))  snprintf(out, n, "<span foreground=\"%s\">הושלם חלקית · %s</span>", warn, m->error[0] ? e : "מגירה אחת לא נכתבה");
        else if (!strcmp(m->state, "done"))     snprintf(out, n, "<span foreground=\"%s\">הסתיים</span>", ok);
        else if (!m->state[0] || !strcmp(m->state, "waiting")) snprintf(out, n, "מחכה לשידור");
        else if (m->error[0])                   snprintf(out, n, "%s · <span foreground=\"%s\">%s</span>", lbl, bad, e);
        else                                    snprintf(out, n, "%s", lbl);
        return;
    }
    if (m->awake) snprintf(out, n, "<span foreground=\"%s\">ער · %d מגירות מוכנות</span>", ok, m->fresh_drawers);
    else          snprintf(out, n, "כבוי");
}

/* Measure the rows for a box <w> wide. .room-row: padding 10px 14px, gap 10,
 * 13.5px, border-bottom 1 (not on the last); led 8; <b> min-width 90;
 * .dev 12px mono. */
void rows_make(App *a, cairo_t *cr, double w, int mode, const char *empty, RowSet *rs) {
    const State *s = &a->st;
    memset(rs, 0, sizeof *rs);
    rs->n = s->nmachines;
    if (rs->n == 0) {
        rs->empty = sub_make(cr, empty, w - 2);
        rs->h = 2 + rs->empty.h;
        return;
    }
    double inner = w - 2*N_BORDER - 2*N_FORM_GAP;
    for (int i = 0; i < rs->n; i++) {
        const Machine *m = &s->machines[i];
        Row *r = &rs->r[i];
        char mk[1024];
        r->name = text_make(cr, FONT_SANS, N_SUB, 700, m->name[0] ? m->name : m->mac, 0, DIR_RTL);
        r->mac  = text_make(cr, FONT_MONO, N_SUB, 400, m->mac, 0, DIR_LTR);
        double used = N_ACTION_GAP + N_ACTION_GAP + dmax(N_DONE_ICON, r->name.w) + N_ACTION_GAP + r->mac.w + N_ACTION_GAP;
        room_status_markup(a, m, mode, mk, sizeof mk);
        r->state = text_make_markup(cr, FONT_SANS, N_SUB, 400, mk, (int)dmax(N_MESSAGE_ICON, inner - used), DIR_RTL);
        double line = dmax(dmax(r->name.h, r->mac.h), dmax(r->state.h, N_ACTION_GAP));
        r->h = N_ACTION_GAP + line + N_ACTION_GAP + (i < rs->n - 1 ? 1 : 0);
        r->on  = mode == ROWS_CLASS || m->awake || m->joined;
        r->dim = mode != ROWS_CLASS && !r->on;
        rs->h += r->h;
    }
    rs->h += 2;                                           /* the box's border */
}

void rows_draw(App *a, cairo_t *cr, double x, double y, double w, RowSet *rs) {
    const Theme *t = a->theme;
    Rect box = { x, y, w, rs->h };
    draw_fill_rrect(cr, box, RADIUS_SM, t->choice);
    draw_border_rrect(cr, box, RADIUS_SM, t->choice_line, N_BORDER);
    cairo_save(cr);
    draw_rrect_path(cr, box, RADIUS_SM); cairo_clip(cr);
    if (rs->n == 0) {
        text_draw(cr, &rs->empty, x + 1, y + 1, t->ink);
        text_free(&rs->empty);
        cairo_restore(cr);
        return;
    }
    double ry = y + 1;
    for (int i = 0; i < rs->n; i++) {
        Row *r = &rs->r[i];
        if (r->dim) cairo_push_group(cr);                 /* .room-row.dim{opacity:.55} */
        double cy = ry + (r->h - (i < rs->n - 1 ? 1 : 0)) / 2;
        double lx = x + w - N_BORDER - N_FORM_GAP - N_ACTION_GAP/2;
        if (r->on) draw_led(cr, lx, cy, N_ACTION_GAP, t->led_ok);
        else { cairo_arc(cr, lx, cy, N_ACTION_GAP/2, 0, 2 * M_PI); draw_set(cr, t->hair); cairo_fill(cr); }
        double nx = lx - N_ACTION_GAP/2 - N_ACTION_GAP;
        text_draw_r(cr, &r->name, nx, cy - r->name.h / 2, t->ink);
        double mx = nx - dmax(N_DONE_ICON, r->name.w) - N_ACTION_GAP;        /* right edge of .dev */
        text_draw_r(cr, &r->mac, mx, cy - r->mac.h / 2, t->ink);
        text_draw(cr, &r->state, x + N_BORDER + N_FORM_GAP, cy - r->state.h / 2, t->ink);
        if (r->dim) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, N_DIM_ALPHA); }
        if (i < rs->n - 1) {
            cairo_rectangle(cr, x, ry + r->h - 1, w, 1);
            draw_set(cr, t->hair); cairo_fill(cr);
        }
        ry += r->h;
        text_free(&r->name); text_free(&r->mac); text_free(&r->state);
    }
    cairo_restore(cr);
}

/* ---- #410: elapsed / rate / ETA / freshness, in words ----------------------- */

static void hms(int secs, char *out, size_t n) {
    if (secs >= 3600) snprintf(out, n, "%d:%02d:%02d", secs / 3600, secs % 3600 / 60, secs % 60);
    else              snprintf(out, n, "%d:%02d", secs / 60, secs % 60);
}

void pace_text(int elapsed_s, long long rate_bps, int eta_s, int latin, char *out, size_t n) {
    /* Each number is printed only when it was measured: a rate of 0 reads
     * as "stopped", and a wave with no measurement yet is not stopped. */
    char el[24], rt[32], eta[32]; size_t len = 0;
    out[0] = 0;
    if (elapsed_s >= 0) {
        hms(elapsed_s, el, sizeof el);
        len += snprintf(out + len, n - len, latin ? "elapsed %s" : "עבר %s", el);
    }
    if (rate_bps > 0) {
        fmt_bytes(rt, sizeof rt, (double)rate_bps);
        len += snprintf(out + len, n - len, latin ? "%s%s/s" : "%s%s/שנ'", len ? " · " : "", rt);
    }
    if (eta_s >= 0 && rate_bps > 0) {
        hms(eta_s, eta, sizeof eta);
        len += snprintf(out + len, n - len, latin ? "%sETA %s" : "%sנותרו ~%s", len ? " · " : "", eta);
    } else if (len) {
        len += snprintf(out + len, n - len, latin ? " · ETA not measured" : " · סיום לא נמדד");
    }
}

void pace_age(long long updated, char *out, size_t n) {
    out[0] = 0;
    if (updated <= 0) return;
    long long age = (long long)time(NULL) - updated;
    if (age < 0) age = 0;
    snprintf(out, n, "עודכן לפני %lld שנ'", age);
}

/* ---- the .st-prog-line (b 22px + span.sub, align-items:baseline) --------- */

ProgLine progline_make(cairo_t *cr, const char *big, const char *small, double w) {
    ProgLine p;
    p.big = text_make(cr, FONT_SANS, N_PROGRESS_PCT, 700, big, 0, DIR_RTL);
    p.small = sub_make(cr, small, w / 2);
    p.drop = text_baseline(&p.big) - text_baseline(&p.small);
    p.h = dmax(p.big.h, p.drop + p.small.h);
    return p;
}

void progline_draw(cairo_t *cr, const Theme *t, ProgLine *p, double x, double y, double w) {
    text_draw_r(cr, &p->big, x + w, y, t->ink);
    text_draw(cr, &p->small, x, y + p->drop, t->ink);
    text_free(&p->big); text_free(&p->small);
}

/* ---- the confirm box (room.js #room-confirm / classes.js #cls-confirm) ---- */

Text confirm_label(cairo_t *cr, const char *image, int room, double w) {
    char e[512], mk[768];                          /* image name: 96 bytes escaped, worst case x5 */
    text_escape(image, e, sizeof e);
    if (room) snprintf(mk, sizeof mk, "עצירת הסבב באמצע היא פעולת חירום. הקלידו את שם האימג' המשודר — <b>%s</b> — לאישור:", e);
    else      snprintf(mk, sizeof mk, "עצירת הסבב באמצע היא פעולת חירום. הקלידו את שם האימג' המשודר <b>%s</b> לאישור:", e);
    return text_make_markup(cr, FONT_SANS, N_SUB, 500, mk, (int)w, DIR_RTL);
}

/* Lay the footer buttons from the right edge; ids[] / kinds[] in DOM order. */
void foot_buttons(App *a, cairo_t *cr, Rect foot, Text *labels, const int *kinds, const int *ids, int n) {
    double btn_h = btn_height(cr), bx = foot.x + foot.w - N_PAD, by = foot.y + N_ACTION_TOP;
    for (int i = 0; i < n; i++) {
        double bw = btn_width(&labels[i]);
        bx -= bw;
        draw_btn(a, cr, (Rect){ bx, by, bw, btn_h }, &labels[i], kinds[i], ids[i]);
        bx -= 8;
        text_free(&labels[i]);
    }
}

/* ---- #st-room ---------------------------------------------------------------- */

int image_options(const App *a, const char **out, char (*buf)[168], int max) {
    int n = 0;
    out[n++] = "בחרו אימג'…";
    for (int i = 0; i < a->st.nimages && n < max; i++) {
        const Image *im = &a->st.images[i];
        if (im->folder[0]) snprintf(buf[i], sizeof buf[i], "%s / %s", im->folder, im->name);
        else               snprintf(buf[i], sizeof buf[i], "%s", im->name);
        out[n++] = buf[i];
    }
    return n;
}
