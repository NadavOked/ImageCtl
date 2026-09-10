/* #st-room and #st-class -- the two cards whose bodies room.js and
 * classes.js build at runtime. The text is theirs; the numbers are
 * station.css (.room-machines / .room-row / .cls-bars / .menu-card). The
 * data comes from the --state file; the setup form (image, target) and the
 * confirm box are the operator's. The classroom wizard's steps 2 and 3
 * (machine grid, image list) are not built -- see README. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "widgets.h"

/* ---- .room-machines rows (room.js machineRows / classes.js renderLive) ---- */

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

static void hex_of(Rgb c, char *out, size_t n) {
    snprintf(out, n, "#%02X%02X%02X", (int)(c.r * 255 + 0.5), (int)(c.g * 255 + 0.5), (int)(c.b * 255 + 0.5));
}

/* The status span, as Pango markup (room-ok / room-bad / room-warn / sub). */
static void status_markup(const App *a, const Machine *m, int mode, char *out, size_t n) {
    char ok[8], bad[8], warn[8], e[640], lbl[96];   /* e: 120 bytes escaped, worst case x5 */
    hex_of(a->theme->led_ok, ok, sizeof ok);
    hex_of(ROOM_BAD, bad, sizeof bad);
    hex_of(ROOM_WARN, warn, sizeof warn);
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
static void rows_make(App *a, cairo_t *cr, double w, int mode, const char *empty, RowSet *rs) {
    const State *s = &a->st;
    memset(rs, 0, sizeof *rs);
    rs->n = s->nmachines;
    if (rs->n == 0) {
        rs->empty = sub_make(cr, empty, w - 2);
        rs->h = 2 + rs->empty.h;
        return;
    }
    double inner = w - 2 - 28;
    for (int i = 0; i < rs->n; i++) {
        const Machine *m = &s->machines[i];
        Row *r = &rs->r[i];
        char mk[1024];
        r->name = text_make(cr, FONT_SANS, 13.5, 700, m->name[0] ? m->name : m->mac, 0, DIR_RTL);
        r->mac  = text_make(cr, FONT_MONO, 12, 400, m->mac, 0, DIR_LTR);
        double used = 8 + 10 + dmax(90, r->name.w) + 10 + r->mac.w + 10;
        status_markup(a, m, mode, mk, sizeof mk);
        r->state = text_make_markup(cr, FONT_SANS, 13.5, 400, mk, (int)dmax(60, inner - used), DIR_RTL);
        double line = dmax(dmax(r->name.h, r->mac.h), dmax(r->state.h, 8));
        r->h = 10 + line + 10 + (i < rs->n - 1 ? 1 : 0);
        r->on  = mode == ROWS_CLASS || m->awake || m->joined;
        r->dim = mode != ROWS_CLASS && !r->on;
        rs->h += r->h;
    }
    rs->h += 2;                                           /* the box's border */
}

static void rows_draw(App *a, cairo_t *cr, double x, double y, double w, RowSet *rs) {
    const Theme *t = a->theme;
    Rect box = { x, y, w, rs->h };
    draw_border_rrect(cr, box, 12, t->hair, 1);
    cairo_save(cr);
    draw_rrect_path(cr, box, 12); cairo_clip(cr);
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
        double lx = x + w - 1 - 14 - 4;
        if (r->on) draw_led(cr, lx, cy, 8, t->led_ok);
        else { cairo_arc(cr, lx, cy, 4, 0, 2 * M_PI); draw_set(cr, t->hair); cairo_fill(cr); }
        double nx = lx - 4 - 10;                          /* right edge of <b> */
        text_draw_r(cr, &r->name, nx, cy - r->name.h / 2, t->ink);
        double mx = nx - dmax(90, r->name.w) - 10;        /* right edge of .dev */
        text_draw_r(cr, &r->mac, mx, cy - r->mac.h / 2, t->ink);
        text_draw(cr, &r->state, x + 1 + 14, cy - r->state.h / 2, t->ink);
        if (r->dim) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, 0.55); }
        if (i < rs->n - 1) {
            cairo_rectangle(cr, x, ry + r->h - 1, w, 1);
            draw_set(cr, t->hair); cairo_fill(cr);
        }
        ry += r->h;
        text_free(&r->name); text_free(&r->mac); text_free(&r->state);
    }
    cairo_restore(cr);
}

/* ---- the .st-prog-line (b 22px + span.sub, align-items:baseline) --------- */

typedef struct { Text big, small; double drop, h; } ProgLine;

static ProgLine progline_make(cairo_t *cr, const char *big, const char *small, double w) {
    ProgLine p;
    p.big = text_make(cr, FONT_SANS, 22, 700, big, 0, DIR_RTL);
    p.small = sub_make(cr, small, w / 2);
    p.drop = text_baseline(&p.big) - text_baseline(&p.small);
    p.h = dmax(p.big.h, p.drop + p.small.h);
    return p;
}

static void progline_draw(cairo_t *cr, const Theme *t, ProgLine *p, double x, double y, double w) {
    text_draw_r(cr, &p->big, x + w, y, t->ink);
    text_draw(cr, &p->small, x, y + p->drop, t->ink);
    text_free(&p->big); text_free(&p->small);
}

/* ---- the confirm box (room.js #room-confirm / classes.js #cls-confirm) ---- */

static Text confirm_label(cairo_t *cr, const char *image, int room, double w) {
    char e[512], mk[768];                          /* image name: 96 bytes escaped, worst case x5 */
    text_escape(image, e, sizeof e);
    if (room) snprintf(mk, sizeof mk, "עצירת הסבב באמצע היא פעולת חירום. הקלידו את שם האימג' המשודר — <b>%s</b> — לאישור:", e);
    else      snprintf(mk, sizeof mk, "עצירת הסבב באמצע היא פעולת חירום. הקלידו את שם האימג' המשודר <b>%s</b> לאישור:", e);
    return text_make_markup(cr, FONT_SANS, 12, 500, mk, (int)w, DIR_RTL);
}

/* Lay the footer buttons from the right edge; ids[] / kinds[] in DOM order. */
static void foot_buttons(App *a, cairo_t *cr, Rect foot, Text *labels, const int *kinds, const int *ids, int n) {
    double btn_h = btn_height(cr), bx = foot.x + foot.w - 22, by = foot.y + 1 + 14;
    for (int i = 0; i < n; i++) {
        double bw = btn_width(&labels[i]);
        bx -= bw;
        draw_btn(a, cr, (Rect){ bx, by, bw, btn_h }, &labels[i], kinds[i], ids[i]);
        bx -= 8;
        text_free(&labels[i]);
    }
}

/* ---- #st-room ---------------------------------------------------------------- */

static int image_options(const App *a, const char **out, char (*buf)[168], int max) {
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

void screen_room(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 44, in_h = field_height(cr), btn_h = btn_height(cr);
    int ready = 0;
    for (int i = 0; i < s->nmachines; i++) if (s->machines[i].awake) ready += s->machines[i].fresh_drawers;

    if (!s->has_round) {
        /* ---- renderSetup ---- */
        if (!a->room_target_set) {                        /* value="${ready || 24}", once per skeleton */
            snprintf(a->room_target, sizeof a->room_target, "%d", ready ? ready : 24);
            a->room_target_set = 1;
        }
        Head hd = head_make(cr, "הפצה למחשבי שיכפול",
                            "בוחרים אימג' ויעד כוננים; החדר מתעורר, וכל גל כותב למגירות שמוכנות.", inner);
        Text l1 = label_make(cr, "אימג' לשידור", inner);
        Text l2 = label_make(cr, "כמה כוננים צריך הפעם, סך הכל", inner);
        char rd[96]; snprintf(rd, sizeof rd, "כרגע ערים: %d מגירות מוכנות.", ready);
        Text ready_t = sub_make(cr, rd, inner);
        RowSet rs; rows_make(a, cr, inner, ROWS_ROOM_SETUP, "אין מחשבי שיכפול רשומים — רושמים אותם בקונסולה, במסך המחשבים.", &rs);
        Text err = error_make(cr, s->room_error, inner);
        double err_h = dmax(err.h, ERROR_MIN_H);
        double body_h = 22 + l1.h + 2 + in_h + 20 + l2.h + 2 + in_h + 20 + ready_t.h + 10 + rs.h + 6 + err_h + 22;
        Text labels[3] = { btn_label(cr, "פתח סבב והער את החדר"), btn_label(cr, "העֵר את מחשבי השיכפול"), btn_label(cr, "חזרה") };
        static const int kinds[3] = { BTN_PRIMARY, BTN_PLAIN, BTN_PLAIN };
        static const int ids[3] = { HIT_ROOM_OPEN, HIT_ROOM_WAKE, HIT_BACK };

        Rect body, foot;
        card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
        body_clip_begin(a, cr, body);
        double x = body.x + 22, y = body.y + 22;
        const char *opts[MAX_IMAGES + 1]; char obuf[MAX_IMAGES][168];
        int nopt = image_options(a, opts, obuf, MAX_IMAGES + 1);
        text_draw(cr, &l1, x, y, t->muted);              y += l1.h + 2;
        Rect sel_r = { x, y, inner, in_h };
        draw_select(a, cr, sel_r, opts[a->image_sel < nopt ? a->image_sel : 0], HIT_ROOM_IMAGE);
        y += in_h + 20;
        text_draw(cr, &l2, x, y, t->muted);              y += l2.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->room_target, 0, NULL, HIT_ROOM_TARGET);
        y += in_h + 20;
        text_draw(cr, &ready_t, x, y, t->ink);           y += ready_t.h + 10;
        rows_draw(a, cr, x, y, inner, &rs);              y += rs.h + 6;
        text_draw(cr, &err, x, y, t->danger);
        body_clip_end(a, cr);
        foot_draw_bg(cr, t, foot);
        foot_buttons(a, cr, foot, labels, kinds, ids, 3);
        card_end(a, cr);
        if (a->dd_open == HIT_ROOM_IMAGE) draw_popup(a, cr, sel_r, opts, nopt, a->image_sel);   /* outside the card's clip, topmost */
        text_free(&l1); text_free(&l2); text_free(&ready_t); text_free(&err);
        return;
    }

    /* ---- renderLive ---- */
    char sub[160], count[48], hint[400];
    snprintf(sub, sizeof sub, "משדר: %s · גל %d", s->round_image, s->wave_number);
    snprintf(count, sizeof count, "%d / %d", s->written, s->target);
    if (s->wave_open) {
        int need = s->remaining - s->ready; if (need < 0) need = 0;
        snprintf(hint, sizeof hint, "הגל ממתין: %d מגירות מוכנות, צריך עוד %d — או \"התחל עכשיו\". החלפתם מגירות? הדליקו את המכונות והן יצטרפו.",
                 s->ready, need);
    } else {
        snprintf(hint, sizeof hint, "הגל משדר. מכונה שסיימה — מכבים, מחליפים מגירות, מדליקים.");
    }
    int pct = s->target > 0 ? (int)floor(100.0 * s->written / s->target + 0.5) : 0;

    Head hd = head_make(cr, "הפצה למחשבי שיכפול", sub, inner);
    ProgLine pl = progline_make(cr, count, "כוננים שנכתבו", inner);
    Text hint_t = sub_make(cr, hint, inner);
    RowSet rs; rows_make(a, cr, inner, s->wave_open ? ROWS_ROOM_SETUP : ROWS_ROOM_LIVE,
                         "אין מחשבי שיכפול רשומים — רושמים אותם בקונסולה, במסך המחשבים.", &rs);
    Text cl = { NULL, 0, 0 };
    double confirm_h = 0;
    if (a->room_confirming) {
        cl = confirm_label(cr, s->round_image, 1, inner);
        confirm_h = 6 + cl.h + 2 + in_h + 14;             /* label margin-top 6; input inside it */
    }
    Text err = error_make(cr, s->room_error, inner);
    double err_h = dmax(err.h, ERROR_MIN_H);
    double body_h = 22 + pl.h + 6 + 14 + 10 + hint_t.h + 10 + rs.h + confirm_h + 6 + err_h + 22;
    Text labels[4]; int kinds[4], ids[4], nb = 0;
    if (s->wave_open) { labels[nb] = btn_label(cr, "התחל עכשיו"); kinds[nb] = BTN_PRIMARY; ids[nb++] = HIT_ROOM_START; }
    labels[nb] = btn_label(cr, "העֵר שוב");  kinds[nb] = BTN_PLAIN;  ids[nb++] = HIT_ROOM_WAKE;
    labels[nb] = btn_label(cr, "עצור סבב");  kinds[nb] = BTN_DANGER; ids[nb++] = HIT_ROOM_CLOSE;
    labels[nb] = btn_label(cr, "חזרה");      kinds[nb] = BTN_PLAIN;  ids[nb++] = HIT_BACK;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + 22, y = body.y + 22;
    progline_draw(cr, t, &pl, x, y, inner);              y += pl.h + 6;
    draw_big_bar(cr, t, (Rect){ x, y, inner, 14 }, pct, 0); y += 14 + 10;
    text_draw(cr, &hint_t, x, y, t->ink);                y += hint_t.h + 10;
    rows_draw(a, cr, x, y, inner, &rs);                  y += rs.h;
    if (a->room_confirming) {
        y += 6;
        text_draw(cr, &cl, x, y, t->muted);              y += cl.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->room_confirm, 0, NULL, HIT_ROOM_CONFIRM);
        y += in_h + 14;
        text_free(&cl);
    }
    y += 6;
    text_draw(cr, &err, x, y, t->danger);
    body_clip_end(a, cr);
    foot_draw_bg(cr, t, foot);
    foot_buttons(a, cr, foot, labels, kinds, ids, nb);
    card_end(a, cr);
    text_free(&hint_t); text_free(&err);
}

/* ---- #st-class --------------------------------------------------------------- */

void screen_class(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 44, in_h = field_height(cr), btn_h = btn_height(cr);

    if (!s->has_session) {
        /* ---- renderClassPick (step 1 of the wizard) ---- */
        Head hd = head_make(cr, "הפצה לכיתות", "לאיזו כיתה נפתח הסבב", inner);
        double text_w = inner - 16 - 52 - 14 - 16;
        Text cb[MAX_CLASSES], cs[MAX_CLASSES];
        double ch[MAX_CLASSES], body_h = 22 + 3 + 18;
        Text none = { NULL, 0, 0 };
        if (s->nclasses == 0) {
            none = sub_make(cr, "אין עדיין כיתות — מגדירים אותן בקונסולה, במסך המחשבים.", inner);
            body_h += none.h;
        }
        for (int i = 0; i < s->nclasses; i++) {
            char small[128];
            if (s->classes[i].machines) snprintf(small, sizeof small, "%d מחשבים רשומים", s->classes[i].machines);
            else snprintf(small, sizeof small, "אין מחשבים רשומים — מוסיפים בקונסולה, במסך המחשבים");
            cb[i] = text_make(cr, FONT_SANS, 15, 700, s->classes[i].label, (int)text_w, DIR_RTL);
            cs[i] = text_make(cr, FONT_SANS, 12.5, 400, small, (int)text_w, DIR_RTL);
            ch[i] = 18 + dmax(34, cb[i].h + cs[i].h) + 18 + 2;
            body_h += ch[i] + 10;
        }
        body_h += 22;
        Text labels[1] = { btn_label(cr, "חזרה") };
        static const int kinds[1] = { BTN_PLAIN }, ids[1] = { HIT_BACK };

        Rect body, foot;
        card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
        body_clip_begin(a, cr, body);
        double x = body.x + 22, y = body.y + 22;
        draw_cls_bars(cr, t, x, y, inner, 1);            y += 3 + 18;
        if (s->nclasses == 0) { text_draw(cr, &none, x, y, t->ink); text_free(&none); }
        for (int i = 0; i < s->nclasses; i++) {
            Rect c = { x, y, inner, ch[i] };
            int dim = s->classes[i].machines == 0;
            int hov = !dim && hovered(a, c);              /* .menu-card.dim:hover stays plain */
            if (dim) cairo_push_group(cr);                /* opacity:.55 */
            draw_fill_rrect(cr, c, 12, hov ? t->indigo_soft : t->field);
            draw_border_rrect(cr, c, 12, hov ? t->indigo : t->hair, 1);
            double tx = c.x + c.w - 16 - 52;
            draw_tray(cr, t, tx, c.y + (c.h - 34) / 2, 1);
            double block = cb[i].h + cs[i].h, by = c.y + (c.h - block) / 2;
            text_draw(cr, &cb[i], tx - 14 - text_w, by, t->ink);
            text_draw(cr, &cs[i], tx - 14 - text_w, by + cb[i].h, t->muted);
            if (dim) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, 0.55); }
            hit_add(a, c, HIT_CLASS_BASE + i);
            text_free(&cb[i]); text_free(&cs[i]);
            y += ch[i] + 10;
        }
        body_clip_end(a, cr);
        foot_draw_bg(cr, t, foot);
        foot_buttons(a, cr, foot, labels, kinds, ids, 1);
        card_end(a, cr);
        return;
    }

    /* ---- renderLive ---- */
    char sub[224], joined[48], hint[300];
    snprintf(sub, sizeof sub, "משדר: %s · %s — %s", s->sess_image, s->sess_prefix, s->sess_group);
    snprintf(joined, sizeof joined, "%d / %d", s->joined, s->expected);
    if (s->sess_open)
        snprintf(hint, sizeof hint, "הסבב פתוח — כל מחשב שעולה מצטרף. השידור יתחיל כשכולם יגיעו, בעוד %d:%02d דקות, או בלחיצה.",
                 s->starts_in / 60, s->starts_in % 60);
    else
        snprintf(hint, sizeof hint, "השידור רץ. עומדים בכיתה ורואים מי תקוע — בלי לעבור בין מסכים.");

    Head hd = head_make(cr, "הפצה לכיתות", sub, inner);
    ProgLine pl = progline_make(cr, joined, "מחשבים בסבב", inner);
    Text hint_t = sub_make(cr, hint, inner);
    RowSet rs; rows_make(a, cr, inner, ROWS_CLASS, "עוד לא הצטרף אף מחשב — הכיתה מתעוררת.", &rs);
    Text cl = { NULL, 0, 0 };
    double confirm_h = 0;
    if (a->class_confirming) {
        cl = confirm_label(cr, s->sess_image, 0, inner);
        confirm_h = 6 + cl.h + 2 + in_h + 14;
    }
    Text err = error_make(cr, s->class_error, inner);
    double err_h = dmax(err.h, ERROR_MIN_H);
    double body_h = 22 + 3 + 18 + pl.h + hint_t.h + 10 + rs.h + confirm_h + 6 + err_h + 22;
    Text labels[3]; int kinds[3], ids[3], nb = 0;
    if (s->sess_open) { labels[nb] = btn_label(cr, "התחל עכשיו"); kinds[nb] = BTN_PRIMARY; ids[nb++] = HIT_CLASS_START; }
    labels[nb] = btn_label(cr, "עצור סבב"); kinds[nb] = BTN_DANGER; ids[nb++] = HIT_CLASS_CLOSE;
    labels[nb] = btn_label(cr, "חזרה");     kinds[nb] = BTN_PLAIN;  ids[nb++] = HIT_BACK;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + 22, y = body.y + 22;
    draw_cls_bars(cr, t, x, y, inner, 4);                y += 3 + 18;
    progline_draw(cr, t, &pl, x, y, inner);              y += pl.h;
    text_draw(cr, &hint_t, x, y, t->ink);                y += hint_t.h + 10;
    rows_draw(a, cr, x, y, inner, &rs);                  y += rs.h;
    if (a->class_confirming) {
        y += 6;
        text_draw(cr, &cl, x, y, t->muted);              y += cl.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->class_confirm, 0, NULL, HIT_CLASS_CONFIRM);
        y += in_h + 14;
        text_free(&cl);
    }
    y += 6;
    text_draw(cr, &err, x, y, t->danger);
    body_clip_end(a, cr);
    foot_draw_bg(cr, t, foot);
    foot_buttons(a, cr, foot, labels, kinds, ids, nb);
    card_end(a, cr);
    text_free(&hint_t); text_free(&err);
}
