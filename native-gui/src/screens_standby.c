/* #1304 (Nadav 12/10): the cloner's standby screen -- mockup A "חזית המארז",
 * docs/design/native/standby-a/ (standby-a.html + the four PNGs). One status
 * line on top, then the drawer cage as it stands in the machine (drawer 1 at
 * the top), and IP / MAC / hello in the status bar. No background art.
 *
 * Every value comes from the state file (clonergui.sh): cdisk= (port, model,
 * size, SMART verdict, the server's failure cause), cslot_name= (#704) and
 * hello_age= / hello_rc= (guistate.sh gui_hello_state). What the agent does
 * not send is not drawn -- see the README section of this screen. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "screens_rounds.h"

#define SB_CAGE_W   980.0   /* .A .st / .cage / .note width */
#define SB_BAY_H    132.0   /* .A .bay height */
#define SB_BAY_MIN  76.0    /* a fourth drawer and up shrink the bays to fit, not past this */
#define SB_BAY_GAP  10.0
#define SB_NUM_W    110.0   /* .A .num */
#define SB_GRIP_W   56.0    /* .A .grip */
#define SB_FRESH_S  120     /* a 200 older than this is not "connected" (the menu pill's rule) */

int standby_conn(const State *s) {
    if (s->hello_age < 0) return STANDBY_UNKNOWN;          /* no hello has finished yet */
    if (s->hello_rc == 200 && s->hello_age <= SB_FRESH_S) return STANDBY_OK;
    return STANDBY_OFF;                                     /* no answer, an error, or a stale 200 */
}

/* The drawers drawn: three, as #1170 drew them, and more when a disk or an
 * operator's slot name (#704) sits in a higher port. The real count is the
 * console's drawer_count (#695), and it does not reach the cloner -- so a
 * two-drawer cloner still shows an empty third bay (the cage is laid out
 * for any count; the count is what is missing). */
static int standby_bays(const State *s) {
    int n = 3;
    for (int p = 4; p <= MAX_DRAWERS; p++)
        if (s->cslot_names[p - 1][0] || idle_for_port(s, p)) n = p;
    return n;
}

/* Market size, as on the disk's label: 500107862016 -> "500 GB". */
static void disk_size(unsigned long long b, char *out, size_t n) {
    double v = (double)b;
    if (v >= 999.5e9) snprintf(out, n, "%.3g TB", v / 1e12);
    else              snprintf(out, n, "%.3g GB", v / 1e9);
}

static void ago(int s, char *out, size_t n) {
    if (s < 120) snprintf(out, n, "לפני %d שניות", s);
    else if (s < 7200) snprintf(out, n, "לפני %d דקות", s / 60);
    else snprintf(out, n, "לפני %d שעות", s / 3600);
}

/* The wall-clock time <age> seconds ago; --png counts back from the header's 12:45. */
static void clock_ago(const App *a, int age, char *out, size_t n) {
    if (a->fixed_clock) {
        int t = ((12 * 3600 + 45 * 60 - age) % 86400 + 86400) % 86400;
        snprintf(out, n, "%02d:%02d", t / 3600, t / 60 % 60);
        return;
    }
    time_t then = time(NULL) - age; struct tm tmv;
#ifdef _WIN32
    if (localtime_s(&tmv, &then) == 0) { strftime(out, n, "%H:%M", &tmv); return; }
#else
    if (localtime_r(&then, &tmv)) { strftime(out, n, "%H:%M", &tmv); return; }
#endif
    snprintf(out, n, "--:--");
}

/* .led: a dot with a 4px ring in the soft tone; ring 0 = the idle dot. */
static void sb_led(cairo_t *cr, double cx, double cy, double d, Rgb c, Rgb soft, int ring, double alpha) {
    if (ring) { cairo_arc(cr, cx, cy, d / 2 + 4, 0, 2 * M_PI); draw_set_a(cr, soft, alpha); cairo_fill(cr); }
    cairo_arc(cr, cx, cy, d / 2, 0, 2 * M_PI); draw_set_a(cr, c, alpha); cairo_fill(cr);
}

/* "השרת לא עונה": a red X where the green dot stood. */
static void sb_x(cairo_t *cr, double cx, double cy, Rgb c, Rgb soft) {
    cairo_arc(cr, cx, cy, 12, 0, 2 * M_PI); draw_set(cr, soft); cairo_fill(cr);
    cairo_set_line_width(cr, 2.6); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_move_to(cr, cx - 5, cy - 5); cairo_line_to(cr, cx + 5, cy + 5);
    cairo_move_to(cr, cx + 5, cy - 5); cairo_line_to(cr, cx - 5, cy + 5);
    draw_set(cr, c); cairo_stroke(cr);
}

static Rgb soft_of(const Theme *t, int lvl) {
    return lvl == 2 ? t->danger_soft : lvl == 1 ? t->warning_soft : t->success_soft;
}

/* What the chip says, by the #872 rule (smart_level): failed_last red, fail
 * (and an old agent's warn) orange -- a question comes before writing --
 * the rest green, unchecked said in words. */
static const char *chip_he(const char *smart) {
    if (!strcmp(smart, "passed")) return RLM "SMART תקין";   /* RLM: a Latin-first label stays RTL */
    if (!strcmp(smart, "failed_last")) return "נכשל בשיכפול הקודם";
    if (smart_level(smart) == 1) return "אזהרת SMART";
    return RLM "SMART לא נבדק";
}

/* The line under the drawer's name: why the chip is not green. fail is only
 * ever health_failed (smart.sh smart_classify); failed_last carries the
 * server's cause (#874, the console's FAILURE_CAUSE_HE words). */
static const char *reason_he(const IdleDisk *d) {
    if (!strcmp(d->smart, "fail")) return "בדיקת הבריאות של הדיסק נכשלה";
    if (!strcmp(d->smart, "failed_last"))
        return !strcmp(d->cause, "cable") ? "סיבה: כבל/חריץ" : !strcmp(d->cause, "disk") ? "סיבה: הדיסק" : "סיבה: לא סווגה";
    return NULL;
}

/* .pill: 24px, fully round, 12/600, a 7px dot at the start (the right). Right edge in, width out. */
static double sb_pill(cairo_t *cr, double right, double cy, const char *label, Rgb fg, Rgb bg) {
    Text v = text_make(cr, FONT_SANS, 12, 600, label, 0, DIR_RTL);
    double w = 10 + 7 + 6 + v.w + 10;
    Rect r = { right - w, cy - 12, w, 24 };
    draw_fill_rrect(cr, r, 12, bg);
    cairo_arc(cr, right - 10 - 3.5, cy, 3.5, 0, 2 * M_PI); draw_set(cr, fg); cairo_fill(cr);   /* <i> first: the start */
    text_draw_r(cr, &v, right - 10 - 7 - 6, cy - v.h / 2.0, fg);
    text_free(&v); return w;
}

/* The empty slot: repeating-linear-gradient(135deg, slot 0 10px, sunken 10px 20px). */
static void sb_hatch(cairo_t *cr, const Theme *t, Rect r) {
    int dark = t == &THEME_DARK;
    cairo_save(cr);
    cairo_rectangle(cr, r.x, r.y, r.w, r.h); cairo_clip(cr);
    draw_set(cr, t->art_bg); cairo_paint(cr);
    double d = 20 / sqrt(2.0);
    cairo_pattern_t *p = cairo_pattern_create_linear(r.x, r.y, r.x + d, r.y + d);
    Rgb s = dark ? (Rgb){ 0, 0, 0 } : t->art_a;
    double a = dark ? .18 : .35;
    cairo_pattern_add_color_stop_rgba(p, 0, s.r, s.g, s.b, a);
    cairo_pattern_add_color_stop_rgba(p, .5, s.r, s.g, s.b, a);
    cairo_pattern_add_color_stop_rgba(p, .5, s.r, s.g, s.b, 0);
    cairo_pattern_add_color_stop_rgba(p, 1, s.r, s.g, s.b, 0);
    cairo_pattern_set_extend(p, CAIRO_EXTEND_REPEAT);
    cairo_set_source(cr, p); cairo_paint(cr);
    cairo_pattern_destroy(p);
    cairo_restore(cr);
}

static void sb_bay(App *a, cairo_t *cr, Rect b, int port) {
    const Theme *t = a->theme; const State *s = &a->st;
    const IdleDisk *d = idle_for_port(s, port);
    int lvl = d ? smart_level(d->smart) : 0;
    Rgb col = d ? smart_color(t, d->smart) : t->led_idle;
    Rgb line = t == &THEME_DARK ? t->art_line : t->art_c;   /* --mark-line: #9AADB8 / #4A5D6B */
    double cy = b.y + b.h / 2;

    cairo_save(cr);
    draw_rrect_path(cr, b, 4); cairo_clip(cr);
    draw_set(cr, d ? t->surface : t->porcelain); cairo_paint(cr);
    Rect face = { b.x + SB_GRIP_W + 1, b.y, b.w - SB_GRIP_W - SB_NUM_W - 2, b.h };
    if (!d) sb_hatch(cr, t, face);
    cairo_rectangle(cr, b.x + b.w - SB_NUM_W, b.y, 1, b.h);   /* .num border-inline-end */
    cairo_rectangle(cr, b.x + SB_GRIP_W, b.y, 1, b.h);        /* .grip border-inline-start */
    draw_set(cr, t->hair); cairo_fill(cr);
    if (lvl) { cairo_rectangle(cr, b.x + b.w - 4, b.y, 4, b.h); draw_set(cr, col); cairo_fill(cr); }
    cairo_restore(cr);
    draw_border_rrect(cr, b, 4, lvl ? col : t->hair, 1);

    /* .num: the drawer's number, big, and "מגירה" */
    char num[8]; snprintf(num, sizeof num, "%d", port);
    Text nb = text_make(cr, FONT_MONO, 44, 600, num, 0, DIR_LTR);
    Text nl = text_make(cr, FONT_SANS, 11, 400, "מגירה", 0, DIR_RTL);
    double nh = 44 + 6 + nl.h, ny = cy - nh / 2, nr = b.x + b.w - 20;
    text_draw_r(cr, &nb, nr, ny + (44 - nb.h) / 2.0, t->ink);
    text_draw_r(cr, &nl, nr, ny + 50, t->muted);
    text_free(&nb); text_free(&nl);

    /* .grip: the drawer's LED and the two handle lines */
    double gx = b.x + SB_GRIP_W / 2, gy = cy - 20;
    sb_led(cr, gx, gy + 5, 10, d ? col : t->led_idle, soft_of(t, lvl), d != NULL, 1);
    for (int i = 0; i < 2; i++) {
        draw_fill_rrect(cr, (Rect){ gx - 11, gy + 22 + i * 15, 22, 3 }, 1.5, line);
    }

    /* .face, from the right: the name block, the SMART chip; the size and
     * model (or "ריק") at the left end */
    double fr = face.x + face.w - 26, fl = face.x + 26;
    char name[SLOT_NAME_MAX + 16], sub[SLOT_NAME_MAX + 48];
    cloner_slot_label(s, port, name, sizeof name);
    if (s->cslot_names[port - 1][0]) snprintf(sub, sizeof sub, "דיסק %d · SATA %d", port, port - 1);
    else snprintf(sub, sizeof sub, "SATA %d", port - 1);
    const char *why = d ? reason_he(d) : NULL;
    Text tn = text_make(cr, FONT_SANS, 18, 600, name, 0, DIR_RTL);
    Text ts = text_make(cr, FONT_SANS, 12, 400, sub, 0, DIR_RTL);
    Text tw = why ? text_make(cr, FONT_SANS, 12, 400, why, 0, DIR_RTL) : (Text){ NULL, 0, 0 };
    double bh = tn.h + 3 + ts.h + (why ? 3 + tw.h : 0), by = cy - bh / 2;
    text_draw_r(cr, &tn, fr, by, t->ink); by += tn.h + 3;
    text_draw_r(cr, &ts, fr, by, t->muted); by += ts.h + 3;
    if (why) text_draw_r(cr, &tw, fr, by, col);
    double names_w = fmax(tn.w, fmax(ts.w, tw.w));
    text_free(&tn); text_free(&ts); if (why) text_free(&tw);

    if (!d) {
        Text e = text_make(cr, FONT_SANS, 15, 400, "ריק — אפשר להכניס דיסק", 0, DIR_RTL);
        text_draw(cr, &e, fl, cy - e.h / 2.0, t->muted); text_free(&e);
        return;
    }
    sb_pill(cr, fr - names_w - 22, cy, chip_he(d->smart), col, soft_of(t, lvl));
    char size[32]; disk_size(d->size, size, sizeof size);
    Text sz = text_make(cr, FONT_MONO, 22, 600, size, 0, DIR_LTR);
    Text md = d->model[0] ? text_make(cr, FONT_MONO, 13, 400, d->model, 0, DIR_LTR)
                          : text_make(cr, FONT_SANS, 13, 400, "דגם לא דווח", 0, DIR_RTL);
    double mh = sz.h + 2 + md.h, my = cy - mh / 2;
    text_draw(cr, &sz, fl, my, t->ink);
    text_draw(cr, &md, fl, my + sz.h + 2, t->muted);
    text_free(&sz); text_free(&md);
}

void screen_standby(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme; const State *s = &a->st;
    int conn = standby_conn(s), n = standby_bays(s), present = 0;
    for (int p = 1; p <= n; p++) {
        const IdleDisk *d = idle_for_port(s, p);
        present += d != NULL;
    }
    double cw = fmin(SB_CAGE_W, W - 2 * N_PAD), x0 = (W - cw) / 2, xr = x0 + cw;
    double y = head_h + 34;

    /* .st: the one status line */
    const char *h1 = conn == STANDBY_OK ? "מחובר. ממתין לסבב." : conn == STANDBY_OFF ? "השרת לא עונה" : "ממתין לתשובה מהשרת";
    const char *p1 = conn == STANDBY_OFF ? "המחשב ממשיך לנסות לבד. בדקו שכבל הרשת מחובר ושהשרת דולק."
                                         : "המחשב יצטרף לבד כשייפתח סבב — אין צורך לגעת בו.";
    const char *srv = s->server_name[0] ? s->server_name : s->server_addr[0] ? s->server_addr : "לא ידוע";
    char since[48] = "";
    if (conn == STANDBY_OFF && s->hello_rc == 200) {   /* a stale 200: when it last answered is known */
        char hm[16]; clock_ago(a, s->hello_age, hm, sizeof hm); snprintf(since, sizeof since, "מאז %s", hm);
    }
    Text to1 = text_make(cr, FONT_SANS, 12, 400, conn == STANDBY_OK ? "מחובר אל" : conn == STANDBY_OFF ? "אין תשובה מ" : "מתחבר אל", 0, DIR_RTL);
    Text to2 = text_make(cr, FONT_MONO, 15, 600, srv, 0, DIR_LTR);
    Text to3 = since[0] ? text_make(cr, FONT_SANS, 12, 400, since, 0, DIR_RTL) : (Text){ NULL, 0, 0 };
    double to_w = fmax(to1.w, fmax(to2.w, to3.w)), to_h = to1.h + to2.h + to3.h;
    double tw = cw - 32 - to_w - 24;
    Text th = text_make(cr, FONT_SANS, 30, 600, h1, (int)tw, DIR_RTL);
    Text tp = text_make(cr, FONT_SANS, 14, 400, p1, (int)tw, DIR_RTL);
    /* the CSS line boxes (h1 30px/1.15, p 14px/1.3), not Pango's natural
     * line height -- which is taller and pushed the cage 6px down */
    double hh = th.h * 34.5 / text_line_height(cr, FONT_SANS, 30), ph = tp.h * 18.2 / text_line_height(cr, FONT_SANS, 14);
    double block = hh + 4 + ph, row = fmax(block, to_h), cy = y + row / 2;
    if (conn == STANDBY_OFF) sb_x(cr, xr - 8, cy, t->danger, t->danger_soft);
    else {
        double alpha = 1;   /* .live: breathe 2.4s -- a still frame under --png */
        struct timespec ts;
        if (conn == STANDBY_OK && !a->fixed_clock && timespec_get(&ts, TIME_UTC) == TIME_UTC)
            alpha = 1 - .55 * (.5 - .5 * cos((ts.tv_sec % 600 + ts.tv_nsec / 1e9) * 2 * M_PI / 2.4));
        sb_led(cr, xr - 8, cy, 16, conn == STANDBY_OK ? t->led_ok : t->led_idle, t->success_soft, conn == STANDBY_OK, alpha);
    }
    double ty = cy - block / 2;
    text_draw_r(cr, &th, xr - 32, ty + (hh - th.h) / 2, conn == STANDBY_OFF ? t->danger : t->ink);
    text_draw_r(cr, &tp, xr - 32, ty + hh + 4 + (ph - tp.h) / 2, t->muted);
    double oy = cy - to_h / 2;
    text_draw(cr, &to1, x0, oy, t->muted); oy += to1.h;
    text_draw(cr, &to2, x0, oy, t->ink); oy += to2.h;
    if (to3.layout) text_draw(cr, &to3, x0, oy, t->muted);
    text_free(&to1); text_free(&to2); if (to3.layout) text_free(&to3); text_free(&th); text_free(&tp);
    y = floor(y + row + 26 + .5);

    /* .note lines under the cage, measured first: the bays shrink to leave them room */
    Text notes[MAX_DRAWERS]; int nn = 0; double notes_h = 0;
    for (int p = 1; p <= n && nn < MAX_DRAWERS; p++) {
        const IdleDisk *d = idle_for_port(s, p);
        if (!d || !smart_level(d->smart)) continue;
        char label[SLOT_NAME_MAX + 16], msg[SLOT_NAME_MAX + 120];
        cloner_slot_label(s, p, label, sizeof label);
        snprintf(msg, sizeof msg, RLM "%s: %s — לפני כתיבה אליו תופיע שאלה.", label, chip_he(d->smart));
        notes[nn] = text_make(cr, FONT_SANS, 13, 400, msg, (int)(cw - 18), DIR_RTL);
        notes_h += (nn ? 6 : 14) + notes[nn].h; nn++;
    }

    /* .cage */
    Text lr = text_make(cr, FONT_SANS, 12, 400, "מגירות הדיסקים במחשב — מלמעלה למטה", 0, DIR_RTL);
    char cnt[48]; snprintf(cnt, sizeof cnt, "%d מתוך %d עם דיסק", present, n);
    Text ll = text_make(cr, FONT_SANS, 12, 400, cnt, 0, DIR_RTL);
    double lh = 16;   /* .lbl: 12px/1.3, on whole pixels so every hairline below is one crisp row */
    double chrome = 1 + 14 + lh + 10 + 14 + 1, bay_h = SB_BAY_H;
    double room = H - N_STATUS_H - 24 - notes_h - y - chrome - (n - 1) * SB_BAY_GAP;
    if (n * bay_h > room) bay_h = fmax(SB_BAY_MIN, floor(room / n));
    Rect cage = { x0, y, cw, chrome + n * bay_h + (n - 1) * SB_BAY_GAP };
    if (conn == STANDBY_OFF) cairo_push_group(cr);   /* [data-conn=off] .cage{opacity:.72} */
    int dark = t == &THEME_DARK;
    draw_box_shadow(cr, cage, 6, 10, 24, -14, dark ? (Rgb){ 0, 0, 0 } : t->header, dark ? .40 : .14);
    draw_fill_rrect(cr, cage, 6, t->surface); draw_border_rrect(cr, cage, 6, t->hair, 1);
    double by = cage.y + 15;
    text_draw_r(cr, &lr, cage.x + cage.w - 18, by + (lh - lr.h) / 2, t->muted);
    text_draw(cr, &ll, cage.x + 18, by + (lh - ll.h) / 2, t->muted);
    by += lh + 10;
    for (int p = 1; p <= n; p++) {
        sb_bay(a, cr, (Rect){ cage.x + 15, by, cage.w - 30, bay_h }, p);
        by += bay_h + SB_BAY_GAP;
    }
    if (conn == STANDBY_OFF) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, .72); }
    text_free(&lr); text_free(&ll);

    y = cage.y + cage.h;
    for (int i = 0, p = 1; i < nn; p++) {
        const IdleDisk *d = idle_for_port(s, p);
        if (!d || !smart_level(d->smart)) continue;
        y += i ? 6 : 14;
        Rgb c = smart_color(t, d->smart);
        sb_led(cr, xr - 5, y + notes[i].h / 2.0, 10, c, soft_of(t, smart_level(d->smart)), 1, 1);
        text_draw_r(cr, &notes[i], xr - 18, y, c);
        y += notes[i].h; text_free(&notes[i]); i++;
    }
}

/* The status bar's left end on this screen: IP · MAC · the last hello, in
 * place of the key list (the screen takes no keys). Drawn from <left>. */
void standby_status_info(App *a, cairo_t *cr, double left, double cy) {
    const Theme *t = a->theme; const State *s = &a->st;
    char hello[160], when[48];
    if (s->hello_age < 0) snprintf(hello, sizeof hello, "טרם הסתיים hello ראשון");   /* RLM below: Latin-first lines stay RTL */
    else {
        ago(s->hello_age, when, sizeof when);
        if (s->hello_rc == 200 && standby_conn(s) == STANDBY_OK) snprintf(hello, sizeof hello, "השרת ענה hello 200 · %s", when);
        else if (s->hello_rc == 200) { char hm[16]; clock_ago(a, s->hello_age, hm, sizeof hm); snprintf(hello, sizeof hello, RLM "hello אחרון שנענה: %s", hm); }
        else if (s->hello_rc >= 0) snprintf(hello, sizeof hello, "השרת ענה hello %d · %s", s->hello_rc, when);
        else snprintf(hello, sizeof hello, RLM "hello אחרון %s · ללא תשובה", when);
    }
    Text h = text_make(cr, FONT_SANS, 12, 400, hello, 0, DIR_RTL);
    text_draw(cr, &h, left, cy - h.h / 2.0, t->muted); left += h.w + 18;
    const char *k[2] = { "MAC", "IP" }, *v[2] = { a->mac[0] ? a->mac : "—", a->ip[0] ? a->ip : "—" };
    for (int i = 0; i < 2; i++) {
        Text tv = text_make(cr, FONT_MONO, 12, 400, v[i], 0, DIR_LTR);
        Text tk = text_make(cr, FONT_SANS, 12, 400, k[i], 0, DIR_LTR);
        text_draw(cr, &tv, left, cy - tv.h / 2.0, t->ink); left += tv.w + 5;
        text_draw(cr, &tk, left, cy - tk.h / 2.0, t->muted); left += tk.w + 18;
        text_free(&tv); text_free(&tk);
    }
    text_free(&h);
}
