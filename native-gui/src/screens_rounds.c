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

/* ---- #695: visual room machine grid ------------------------------------- */

typedef struct {
    int cols, rows;
    double gap, card_w, item_h, name_h, card_h, slot_h, total_h;
} RoomGrid;

static const RoomDrawer *room_drawer_at(const Machine *m, int port) {
    for (int i = 0; i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present && m->room_drawers[i].port == port)
            return &m->room_drawers[i];
    return NULL;
}

static int room_selection_override(const App *a, const Machine *m, int port,
                                   int initial) {
    for (int i = 0; i < a->nroom_selection; i++)
        if (a->room_selection[i].port == port &&
            strcmp(a->room_selection[i].mac, m->mac) == 0)
            return a->room_selection[i].selected;
    return initial;
}

/* #872 (Nadav, 16/09): three colours, one rule, shared by the room grid and
 * the idle cloner list. failed_last (the on-disk mark of #845 -- it failed
 * the previous clone, proven) is red; fail (SMART overall-health FAILED,
 * what the BIOS shows) is orange and asks; everything else is green --
 * including unchecked, which says so in words, not in colour. warn no
 * longer comes from the agent; an old agent's warn reads as orange. */
static Rgb smart_color(const Theme *t, const char *smart) {
    if (!strcmp(smart, "failed_last")) return ROOM_BAD;
    if (!strcmp(smart, "fail") || !strcmp(smart, "warn")) return ROOM_WARN;
    return t->led_ok;
}

/* Machines sit side by side, right to left, and wrap to a new row only when
 * the current one is full: two or three cloners are a single row, thirty wrap
 * into several. Card height fits the deepest machine's drawer stack, so the
 * disks read as one comfortable rectangle rather than filling the whole card. */
static RoomGrid room_grid_make(cairo_t *cr, double w, int n, int max_slots,
                               double budget_h) {
    RoomGrid g;
    memset(&g, 0, sizeof g);
    g.gap = 7;
    g.name_h = text_line_height(cr, FONT_SANS, 11);
    if (n <= 0) return g;
    if (max_slots < 1) max_slots = 1;
    if (max_slots > MAX_DRAWERS) max_slots = MAX_DRAWERS;

    int max_cols = (int)floor((w + g.gap) / (54 + g.gap));
    if (max_cols < 1) max_cols = 1;
    int want_cols = (int)floor((w + g.gap) / (78 + g.gap));   /* narrower cards; ~7 fit one row */
    if (want_cols < 1) want_cols = 1;
    if (want_cols > max_cols) want_cols = max_cols;
    g.cols = (n < want_cols) ? n : want_cols;
    g.rows = (n + g.cols - 1) / g.cols;
    g.card_w = (w - (g.cols - 1) * g.gap) / g.cols;
    if (g.card_w > 100) g.card_w = 100;

    g.slot_h = 22;
    g.card_h = 10 + max_slots * g.slot_h + (max_slots - 1);
    g.item_h = g.name_h + 3 + g.card_h;
    g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
    if (g.total_h > budget_h) {                              /* many rows: shrink slots to fit */
        double avail = (budget_h - (g.rows - 1) * g.gap) / g.rows - g.name_h - 3;
        g.slot_h = floor((avail - 10 - (max_slots - 1)) / max_slots);
        if (g.slot_h < 8) g.slot_h = 8;
        g.card_h = 10 + max_slots * g.slot_h + (max_slots - 1);
        g.item_h = g.name_h + 3 + g.card_h;
        g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
    }
    return g;
}

/* #714: are all present disks of this machine currently selected? */
static int room_machine_all_selected(const App *a, const Machine *m) {
    int any = 0;
    for (int i = 0; i < m->nroom_drawers; i++) {
        const RoomDrawer *d = &m->room_drawers[i];
        if (!d->present) continue;
        any = 1;
        if (!room_selection_override(a, m, d->port, d->selected)) return 0;
    }
    return any;
}

static void room_machine_draw(App *a, cairo_t *cr, const Machine *m, int mi,
                              Rect item, const RoomGrid *g) {
    const Theme *t = a->theme;
    const char *name = m->name[0] ? m->name : m->mac;
    Text nt = text_make(cr, FONT_SANS, 11, 600, name, 0, DIR_RTL);   /* natural width */
    /* The name is a button (#714): click it to select/clear every disk of the
     * machine. It fills indigo when they are all selected, so the state shows. */
    int all_sel = m->awake && room_machine_all_selected(a, m);
    double pill_w = nt.w + 16; if (pill_w > item.w) pill_w = item.w;
    Rect pill = { item.x + (item.w - pill_w) / 2, item.y, pill_w, g->name_h };
    if (m->awake) {
        draw_fill_rrect(cr, pill, 5, all_sel ? t->indigo : t->field);
        draw_border_rrect(cr, pill, 5, all_sel ? t->indigo : t->hair, 1);
    }
    cairo_save(cr);
    cairo_rectangle(cr, pill.x, pill.y, pill.w, g->name_h);
    cairo_clip(cr);
    text_draw(cr, &nt, item.x + (item.w - nt.w) / 2, item.y,
              all_sel ? t->on_ink : t->ink);
    cairo_restore(cr);
    text_free(&nt);
    if (m->awake)
        hit_add(a, pill, HIT_ROOM_MACHINE_BASE + mi);

    Rect card = { item.x, item.y + g->name_h + 3, item.w, g->card_h };
    draw_fill_rrect(cr, card, 7, m->awake ? t->surface : t->track);
    draw_border_rrect(cr, card, 7, t->hair, 1);

    int slots = m->drawer_count;
    if (slots < 1) slots = 1;
    if (slots > MAX_DRAWERS) slots = MAX_DRAWERS;

    double sx = card.x + 5, sy = card.y + 5, sw = card.w - 10;
    for (int port = 1; port <= slots; port++) {
        const RoomDrawer *d = room_drawer_at(m, port);
        Rect slot = { sx, sy + (port - 1) * (g->slot_h + 1), sw, g->slot_h };
        int selected = d && room_selection_override(a, m, port, d->selected);
        Rgb fill = t->led_idle;
        if (m->awake && d)
            fill = smart_color(t, d->smart);

        draw_fill_rrect(cr, slot, 3, fill);
        draw_border_rrect(cr, slot, 3, selected ? t->indigo : t->hair,
                          selected ? 2 : 1);

        if (m->awake && d) {
            char capacity[24];
            fmt_bytes(capacity, sizeof capacity, (double)d->size_bytes);
            Text ct = text_make(cr, FONT_SANS, g->slot_h >= 13 ? 9.5 : 8,
                                selected ? 700 : 600, capacity, 0, DIR_LTR);
            cairo_save(cr);
            cairo_rectangle(cr, slot.x + 2, slot.y + 1, slot.w - 4, slot.h - 2);
            cairo_clip(cr);
            text_draw(cr, &ct, slot.x + (slot.w - ct.w) / 2,
                      slot.y + (slot.h - ct.h) / 2, t->on_ink);
            cairo_restore(cr);
            text_free(&ct);
        }

        /* Empty slots and every slot on an off machine are display-only. */
        if (m->awake && d)
            hit_add(a, slot, HIT_ROOM_DISK_BASE + mi * MAX_DRAWERS + (port - 1));
    }
}

/* First machine is physically rightmost. Index increases leftward, then the
 * next visual row begins again at the right. */
static void room_grid_draw(App *a, cairo_t *cr, double x, double y,
                           double w, const RoomGrid *g) {
    if (a->st.nmachines == 0) {
        Text empty = sub_make(cr,
            "אין מכונות שיכפול רשומות — מוסיפים אותן בקונסולה, ואז מעירים אותן.", w);
        text_draw_r(cr, &empty, x + w, y, a->theme->muted);
        text_free(&empty);
        return;
    }
    for (int i = 0; i < a->st.nmachines; i++) {
        int row = i / g->cols, col = i % g->cols;
        double mx = x + w - g->card_w - col * (g->card_w + g->gap);
        double my = y + row * (g->item_h + g->gap);
        room_machine_draw(a, cr, &a->st.machines[i], i,
                          (Rect){ mx, my, g->card_w, g->item_h }, g);
    }
}

void screen_room(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 44, in_h = field_height(cr), btn_h = btn_height(cr);
    int ready = 0;
    for (int i = 0; i < s->nmachines; i++) if (s->machines[i].awake) ready += s->machines[i].fresh_drawers;

    if (!s->has_round) {
        /* ---- renderSetup ---- */
        /* #715: MODE_DIRECT is this screen without the image select and the
         * drive-count field -- the source is this machine's disk, and the
         * target is exactly the drawers ticked in the grid (a single round). */
        int direct = a->mode == MODE_DIRECT;
        if (!a->room_target_set) {                        /* value="${ready || 24}", once per skeleton */
            snprintf(a->room_target, sizeof a->room_target, "%d", ready ? ready : 24);
            a->room_target_set = 1;
        }
        Head hd = direct
            ? head_make(cr, "הפצה מהדיסק הזה למחשבי השיכפול",
                        "הדיסק של המחשב הזה נקרא פעם אחת (קריאה בלבד) ומשודר ישירות למגירות שמסומנות למטה — בלי לשמור אימג' בשרת. סבב יחיד.", inner)
            : head_make(cr, "הפצה למחשבי שיכפול",
                        "בוחרים אימג' ויעד כוננים; החדר מתעורר, וכל גל כותב למגירות שמוכנות.", inner);
        Text l1 = label_make(cr, direct ? "המקור: הדיסק הפנימי של המחשב הזה" : "אימג' לשידור", inner);
        Text l2 = label_make(cr, "כמה כוננים צריך הפעם, סך הכל", inner);
        char rd[96]; snprintf(rd, sizeof rd, "כרגע ערים: %d מגירות מוכנות.", ready);
        Text ready_t = sub_make(cr, rd, inner);
        int max_slots = 1;
        for (int i = 0; i < s->nmachines; i++)
            if (s->machines[i].drawer_count > max_slots) max_slots = s->machines[i].drawer_count;
        RoomGrid grid = room_grid_make(cr, inner, s->nmachines, max_slots, H >= 900 ? 355 : 285);
        Text err = error_make(cr, s->room_error, inner);
        double err_h = dmax(err.h, ERROR_MIN_H);
        double grid_h = s->nmachines ? grid.total_h : text_line_height(cr, FONT_SANS, 14);
        double sel_all_h = s->nmachines ? btn_h : 0;
        double ready_line_h = dmax(ready_t.h, sel_all_h);
        double fields_h = direct ? l1.h + 20 : l1.h + 2 + in_h + 20 + l2.h + 2 + in_h + 20;
        double body_h = 22 + fields_h + ready_line_h + 10 + grid_h + 6 + err_h + 22;
        Text labels[3] = { btn_label(cr, direct ? "פתח סבב והער את המחשבים שנבחרו" : "פתח סבב והער את החדר"), btn_label(cr, "העֵר את מחשבי השיכפול"), btn_label(cr, "חזרה") };
        static const int kinds[3] = { BTN_PRIMARY, BTN_PLAIN, BTN_PLAIN };
        static const int ids[3] = { HIT_ROOM_OPEN, HIT_ROOM_WAKE, HIT_BACK };

        Rect body, foot;
        card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
        body_clip_begin(a, cr, body);
        double x = body.x + 22, y = body.y + 22;
        const char *opts[MAX_IMAGES + 1]; char obuf[MAX_IMAGES][168];
        int nopt = image_options(a, opts, obuf, MAX_IMAGES + 1);
        Rect sel_r = { x, y + l1.h + 2, inner, in_h };
        if (direct) {
            text_draw_r(cr, &l1, x + inner, y, t->ink);  y += l1.h + 20;
        } else {
            text_draw(cr, &l1, x, y, t->muted);              y += l1.h + 2;
            draw_select(a, cr, sel_r, opts[a->image_sel < nopt ? a->image_sel : 0], HIT_ROOM_IMAGE);
            y += in_h + 20;
            text_draw(cr, &l2, x, y, t->muted);              y += l2.h + 2;
            draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->room_target, 0, NULL, HIT_ROOM_TARGET);
            y += in_h + 20;
        }
        /* ready count on the right; a select/clear-all button on the left (#714) */
        if (s->nmachines) {
            Text sa = btn_label(cr, "בחר / נקה הכל");
            double saw = btn_width(&sa);
            draw_btn(a, cr, (Rect){ x, y, saw, btn_h }, &sa, BTN_PLAIN, HIT_ROOM_SELECT_ALL);
            text_free(&sa);
        }
        text_draw_r(cr, &ready_t, x + inner, y + (ready_line_h - ready_t.h) / 2, t->ink);
        y += ready_line_h + 10;
        room_grid_draw(a, cr, x, y, inner, &grid);       y += grid_h + 6;
        text_draw(cr, &err, x, y, t->danger);
        body_clip_end(a, cr);
        foot_draw_bg(cr, t, foot);
        foot_buttons(a, cr, foot, labels, kinds, ids, 3);
        card_end(a, cr);
        if (!direct && a->dd_open == HIT_ROOM_IMAGE) draw_popup(a, cr, sel_r, opts, nopt, a->image_sel);   /* outside the card's clip, topmost */
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

/* ---- the cloner screen (clonergui.sh, native-only) --------------------------
 *
 * A cloning machine writes 2-3 drawers at once and now has a screen. It shows
 * one row per drawer -- sorted and labelled by SATA port, never by device
 * order (roomflow.sh: sdb is not "disk 2") -- each with its own big bar, and
 * a dominant SMART panel when a drawer failed its health check. Unlike
 * screen_room this reads per-drawer progress (Drawer), not the aggregate
 * Machine.pct. */

static const char *drawer_state_he(const char *s) {
    if (!strcmp(s, "writing"))   return "כותב";
    if (!strcmp(s, "verifying")) return "מאמת";
    if (!strcmp(s, "done"))      return "הסתיים";
    if (!strcmp(s, "failed"))    return "נכשל";
    if (!strcmp(s, "skipped"))   return "דולג";
    if (!strcmp(s, "replacing")) return "להחלפה";
    return "ממתין";                                   /* waiting / empty */
}

/* "דיסק N" from the port, or the device name when no slot was derived. */
static void drawer_name(const Drawer *d, char *out, size_t n) {
    if (d->port > 0) snprintf(out, n, "דיסק %d", d->port);
    else             snprintf(out, n, "דיסק %s", d->dev);
}

/* Order to draw the drawers in: by port ascending, port 0 last. Stable. */
static void drawer_order(const State *s, int *idx) {
    for (int i = 0; i < s->ndrawers; i++) idx[i] = i;
    for (int i = 1; i < s->ndrawers; i++)
        for (int j = i; j > 0; j--) {
            int a = idx[j - 1], b = idx[j];
            int pa = s->drawers[a].port ? s->drawers[a].port : 1 << 20;
            int pb = s->drawers[b].port ? s->drawers[b].port : 1 << 20;
            if (pb < pa) { idx[j - 1] = b; idx[j] = a; } else break;
        }
}

void screen_cloner(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 44, btn_h = btn_height(cr);

    const char *hsub = s->cloner_image[0] ? s->cloner_image : "ממתין לסבב שיכפול";
    Head hd = head_make(cr, "מחשב שיכפול", hsub, inner);

    int idx[MAX_DRAWERS];
    drawer_order(s, idx);

    /* ---- the SMART panel (measured first, drawn on top of dimmed rows) ---- */
    int panel = s->smart_pending;
    Text sp_title = { NULL, 0, 0 }, sp_sub = sp_title;
    Text sp_btn[3] = { { NULL, 0, 0 }, { NULL, 0, 0 }, { NULL, 0, 0 } };
    double panel_h = 0, panel_pad = 16;
    /* #872: a red disk (failed_last) has no "write anyway" -- replace, or
     * continue without it (= skip). Two buttons; the agent maps the click. */
    int red = panel && !strcmp(s->smart_verdict, "failed_last");
    int nbtn = red ? 2 : 3;
    if (panel) {
        char pt[96], ps[256];
        const char *what = red ? "נכשל בשיכפול הקודם" : "בעיית SMART";
        if (s->smart_port > 0) snprintf(pt, sizeof pt, "דיסק %d — %s", s->smart_port, what);
        else                   snprintf(pt, sizeof pt, "דיסק — %s", what);
        if (red)
            /* #874: the reason is the server's cause -- cable/slot or the disk. */
            snprintf(ps, sizeof ps, "%s — כבו, החליפו את הדיסק והדליקו, או המשיכו בלעדיו.",
                     !strcmp(s->smart_reason, "cable") ? "נכשל בכתיבה הקודמת · כבל/חריץ SATA"
                     : !strcmp(s->smart_reason, "disk") ? "נכשל בכתיבה הקודמת · הדיסק"
                     : "הדיסק נכשל בכתיבה הקודמת");
        else
            snprintf(ps, sizeof ps, "%s · %s — כבו, החליפו את הדיסק והדליקו, או בחרו אחרת.",
                     !strcmp(s->smart_verdict, "warn") ? "אזהרת בריאות" : "נכשל בבדיקה",
                     s->smart_reason[0] ? s->smart_reason : "unknown");
        sp_title = text_make(cr, FONT_SANS, 16, 700, pt, (int)(inner - 2 * panel_pad), DIR_RTL);
        sp_sub   = text_make(cr, FONT_SANS, 13, 400, ps, (int)(inner - 2 * panel_pad), DIR_RTL);
        sp_btn[0] = btn_label(cr, "החלף דיסק");
        if (red) sp_btn[1] = btn_label(cr, "המשך");
        else   { sp_btn[1] = btn_label(cr, "כתוב בכל זאת"); sp_btn[2] = btn_label(cr, "דלג"); }
        panel_h = panel_pad + sp_title.h + 6 + sp_sub.h + 12 + btn_h + panel_pad;
    }

    /* ---- the drawer rows (measured) ---- */
    Text d_name[MAX_DRAWERS], d_slot[MAX_DRAWERS], d_stat[MAX_DRAWERS], d_err[MAX_DRAWERS];
    double row_h[MAX_DRAWERS];
    int pcts[MAX_DRAWERS];
    double rows_h = 0;
    for (int k = 0; k < s->ndrawers; k++) {
        const Drawer *d = &s->drawers[idx[k]];
        char nm[40], slot[24], stat[128], b1[32], b2[32];
        drawer_name(d, nm, sizeof nm);
        if (d->port > 0) snprintf(slot, sizeof slot, "SATA %d", d->port - 1);
        else             slot[0] = 0;
        fmt_bytes(b1, sizeof b1, (double)d->bytes);
        fmt_bytes(b2, sizeof b2, (double)d->total);
        pcts[k] = d->total > 0 ? (int)floor(100.0 * d->bytes / d->total + 0.5) : -1;
        char pc[16]; if (pcts[k] >= 0) snprintf(pc, sizeof pc, "%d%%", pcts[k]); else snprintf(pc, sizeof pc, "--");
        snprintf(stat, sizeof stat, "%s · %s / %s · %s", drawer_state_he(d->state), b1, b2, pc);
        d_name[k] = text_make(cr, FONT_SANS, 15, 700, nm, 0, DIR_RTL);
        d_slot[k] = slot[0] ? text_make(cr, FONT_MONO, 12, 400, slot, 0, DIR_LTR) : (Text){ NULL, 0, 0 };
        d_stat[k] = text_make(cr, FONT_SANS, 13, 400, stat, 0, DIR_RTL);
        d_err[k]  = d->error[0] ? error_make(cr, d->error, inner) : (Text){ NULL, 0, 0 };
        double head_line = dmax(d_name[k].h, dmax(d_slot[k].h, d_stat[k].h));
        row_h[k] = head_line + 6 + 14 + (d->error[0] ? 4 + d_err[k].h : 0);
        rows_h += row_h[k] + 12;
    }
    /* Idle (no round yet): show the physically connected disks -- one line per
     * disk, "דיסק N (SATA X): brand · size" -- so the operator sees what is
     * plugged in before a broadcast starts, instead of a bare "waiting". */
    Text idle_line[MAX_DRAWERS]; int n_idle = 0; double idle_h = 0;
    Text empty = { NULL, 0, 0 };
    if (s->ndrawers == 0 && !panel && s->nidisks > 0) {
        for (int k = 0; k < s->nidisks; k++) {
            const IdleDisk *id = &s->idisks[k];
            char sz[32], ln[256];   /* #867/#874: room for the failed_last suffix and the cause */
            fmt_bytes(sz, sizeof sz, (double)id->size);
            /* #867/#874: the server's memory is said in words next to the dot,
             * with the cause it classified -- the cable/slot or the disk;
             * #872: so is unchecked -- green, because not-checked is not failed. */
            const char *fl = !strcmp(id->smart, "failed_last") ? " · נכשל בשיכפול הקודם"
                           : !strcmp(id->smart, "unchecked")   ? " · לא נבדק" : "";
            char why[48] = "";
            if (!strcmp(id->cause, "cable")) {
                if (id->port > 0) snprintf(why, sizeof why, " · כבל/חריץ SATA %d", id->port - 1);
                else              snprintf(why, sizeof why, " · כבל/חריץ");
            } else if (!strcmp(id->cause, "disk")) {
                snprintf(why, sizeof why, " · הדיסק");
            }
            if (id->port > 0)
                snprintf(ln, sizeof ln, "דיסק %d (SATA %d): %s · %s%s%s",
                         id->port, id->port - 1, id->model[0] ? id->model : "דיסק", sz, fl, why);
            else
                snprintf(ln, sizeof ln, "%s · %s%s%s", id->model[0] ? id->model : "דיסק", sz, fl, why);
            /* leave 22px on the right for the SMART status square (#709) */
            idle_line[n_idle] = text_make(cr, FONT_SANS, 15, 500, ln, (int)(inner - 22), DIR_RTL);
            idle_h += idle_line[n_idle].h + 10;
            n_idle++;
        }
    } else if (s->ndrawers == 0) {
        empty = sub_make(cr, panel ? "המתן…" : "אין עדיין מגירות בסבב — הן מופיעות כשהשידור מתחיל.", inner);
    }

    double body_h = 22 + (panel ? panel_h + 14 : 0) + rows_h
                  + (n_idle > 0 ? idle_h : (s->ndrawers == 0 ? empty.h : 0)) + 22;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, 0, &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + 22, y = body.y + 22;

    /* Rows first, dimmed while a SMART choice is pending; no row is clickable. */
    if (panel) cairo_push_group(cr);
    if (s->ndrawers == 0) {
        if (n_idle > 0) {
            for (int k = 0; k < n_idle; k++) {
                text_draw_r(cr, &idle_line[k], x + inner - 22, y, t->ink);
                /* #709/#872: green = ok or unchecked, orange = SMART fail, red = failed the previous clone */
                Rect led = { x + inner - 12, y + (idle_line[k].h - 10) / 2, 10, 10 };
                draw_fill_rrect(cr, led, 3, smart_color(t, s->idisks[k].smart));
                y += idle_line[k].h + 10;
                text_free(&idle_line[k]);
            }
        } else { text_draw(cr, &empty, x, y, t->ink); text_free(&empty); }
    }
    for (int k = 0; k < s->ndrawers; k++) {
        const Drawer *d = &s->drawers[idx[k]];
        double line = dmax(d_name[k].h, dmax(d_slot[k].h, d_stat[k].h));
        double cy = y + line / 2;
        text_draw_r(cr, &d_name[k], x + inner, cy - d_name[k].h / 2, t->ink);
        if (d_slot[k].w > 0)
            text_draw_r(cr, &d_slot[k], x + inner - dmax(d_name[k].w, 70) - 10, cy - d_slot[k].h / 2, t->muted);
        Rgb sc = !strcmp(d->state, "failed") ? t->danger
               : !strcmp(d->state, "done")   ? t->led_ok : t->ink;
        text_draw(cr, &d_stat[k], x, cy - d_stat[k].h / 2, sc);
        y += line + 6;
        draw_big_bar(cr, t, (Rect){ x, y, inner, 14 }, pcts[k], d->bytes > 0);
        y += 14;
        if (d->error[0]) { y += 4; text_draw(cr, &d_err[k], x, y, t->danger); y += d_err[k].h; text_free(&d_err[k]); }
        y += 12;
        text_free(&d_name[k]); if (d_slot[k].w > 0) text_free(&d_slot[k]); text_free(&d_stat[k]);
    }
    if (panel) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, 0.55); }

    /* The panel, fully opaque, above the dimmed rows -- the only hit targets. */
    if (panel) {
        double py = body.y + body.h - 22 - panel_h;      /* anchored at the body's foot */
        Rect box = { x, py, inner, panel_h };
        draw_fill_rrect(cr, box, 12, t->field);
        draw_border_rrect(cr, box, 12, t->danger, 1);
        double ty = box.y + panel_pad;
        text_draw_r(cr, &sp_title, box.x + inner - panel_pad, ty, t->ink);   ty += sp_title.h + 6;
        text_draw_r(cr, &sp_sub,   box.x + inner - panel_pad, ty, t->muted); ty += sp_sub.h + 12;
        /* buttons from the right: [Replace] [Rescue] [Skip]; red: [Replace] [Continue=Skip] */
        static const int kinds[3] = { BTN_PRIMARY, BTN_PLAIN, BTN_PLAIN };
        const int ids[3] = { HIT_SMART_REPLACE, red ? HIT_SMART_SKIP : HIT_SMART_RESCUE, HIT_SMART_SKIP };
        double bx = box.x + inner - panel_pad;
        for (int i = 0; i < nbtn; i++) {
            double bw = btn_width(&sp_btn[i]);
            bx -= bw;
            draw_btn(a, cr, (Rect){ bx, ty, bw, btn_h }, &sp_btn[i], kinds[i], ids[i]);
            bx -= 8;
            text_free(&sp_btn[i]);
        }
        text_free(&sp_title); text_free(&sp_sub);
    }
    body_clip_end(a, cr);
    card_end(a, cr);
}

/* ---- #706: restore a library image onto THIS machine's disk ------------------ */

void screen_restore(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 44, in_h = field_height(cr), btn_h = btn_height(cr);

    Head hd = head_make(cr, "משיכת אימג' לכונן המחשב הזה",
                        "אימג' אחד מהספרייה ייכתב על הדיסק של המחשב הזה — כל המידע עליו יימחק.", inner);
    Text l1 = label_make(cr, "אימג' לשחזור", inner);

    /* Target = the first non-removable local disk. Informational only; the
     * agent rediscovers it before writing (never trusts a UI-supplied device). */
    const Disk *target = NULL;
    for (int i = 0; i < s->ndisks; i++)
        if (!s->disks[i].removable) { target = &s->disks[i]; break; }
    int can_start = (s->nimages > 0) && target != NULL;

    char tinfo[220];
    if (target) {
        char sz[32]; fmt_bytes(sz, sizeof sz, (double)target->size_bytes);
        snprintf(tinfo, sizeof tinfo, "דיסק היעד: %s · %s · %s",
                 target->model[0] ? target->model : "דיסק", sz, target->dev);
    } else {
        snprintf(tinfo, sizeof tinfo, "לא נמצא דיסק פנימי לכתיבה — לא ניתן לשחזר.");
    }
    Text tt = sub_make(cr, tinfo, inner);

    Text l2 = { NULL, 0, 0 }, empty = { NULL, 0, 0 };
    double confirm_h = 0, empty_h = 0;
    if (s->nimages == 0) { empty = sub_make(cr, "אין אימג'ים מתאימים למחשב הזה.", inner); empty_h = empty.h + 10; }
    if (can_start) { l2 = label_make(cr, "הקלידו ERASE לאישור מחיקת הדיסק:", inner); confirm_h = l2.h + 2 + in_h + 14; }
    Text err = error_make(cr, s->form_error, inner);
    double err_h = dmax(err.h, ERROR_MIN_H);

    double body_h = 22 + l1.h + 2 + in_h + 16 + tt.h + 12 + empty_h + confirm_h + 6 + err_h + 22;

    Text labels[2]; int kinds[2], ids[2], nb = 0;
    if (can_start) { labels[nb] = btn_label(cr, "התחל שחזור"); kinds[nb] = BTN_DANGER; ids[nb++] = HIT_RESTORE_START; }
    labels[nb] = btn_label(cr, "חזרה"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_BACK;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + 22, y = body.y + 22;

    const char *opts[MAX_IMAGES + 1]; char obuf[MAX_IMAGES][168];
    int nopt = image_options(a, opts, obuf, MAX_IMAGES + 1);
    text_draw(cr, &l1, x, y, t->muted);                          y += l1.h + 2;
    Rect sel_r = { x, y, inner, in_h };
    draw_select(a, cr, sel_r, opts[a->restore_image_sel < nopt ? a->restore_image_sel : 0], HIT_RESTORE_IMAGE);
    y += in_h + 16;
    text_draw_r(cr, &tt, x + inner, y, target ? t->ink : t->danger); y += tt.h + 12;
    if (s->nimages == 0) { text_draw_r(cr, &empty, x + inner, y, t->muted); y += empty_h; text_free(&empty); }
    if (can_start) {
        text_draw(cr, &l2, x, y, t->muted);                     y += l2.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->restore_confirm, 0, NULL, HIT_RESTORE_CONFIRM);
        y += in_h + 14;
    }
    text_draw(cr, &err, x, y, t->danger);
    body_clip_end(a, cr);
    foot_draw_bg(cr, t, foot);
    foot_buttons(a, cr, foot, labels, kinds, ids, nb);
    card_end(a, cr);
    if (a->dd_open == HIT_RESTORE_IMAGE) draw_popup(a, cr, sel_r, opts, nopt, a->restore_image_sel);
    text_free(&l1); if (can_start) text_free(&l2); text_free(&tt); text_free(&err);
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
