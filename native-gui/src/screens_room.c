#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"

/* ---- #695: visual room machine grid ------------------------------------- */

/* nativeRoom .room-node: padding 12, radius 3, border active/warn; .top row
 * = strong 11px name + .room-dot 8px; small 9px status; then the body --
 * the #695 disk slots (setup) or a .native-bar (live). */
typedef struct {
    int cols, rows;
    double gap, card_w, item_h, name_h, small_h, body_h, slot_h, total_h;
} RoomGrid;

static double node_head_h(const RoomGrid *g) { return g->name_h + 2 + g->small_h + N_LABEL_GAP; }

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

/* Machines sit side by side, right to left, and wrap to a new row only when
 * the current one is full: two or three cloners are a single row, thirty wrap
 * into several. Card height fits the deepest machine's drawer stack, so the
 * disks read as one comfortable rectangle rather than filling the whole card. */
/* max_slots 0 = the live grid: the body is one .native-bar, not slots. */
static RoomGrid room_grid_make(cairo_t *cr, double w, int n, int max_slots,
                               double budget_h) {
    RoomGrid g;
    memset(&g, 0, sizeof g);
    g.gap = N_ROOM_GAP;
    g.name_h = dmax(text_line_height(cr, FONT_SANS, N_NODE_TITLE), N_NODE_DOT);
    g.small_h = text_line_height(cr, FONT_SANS, N_NODE_SUB);
    if (n <= 0) return g;
    if (max_slots > MAX_DRAWERS) max_slots = MAX_DRAWERS;

    int want_cols = w >= N_NARROW_W ? 4 : 2;                 /* .room-grid: repeat(4,1fr) */
    g.cols = n < want_cols ? n : want_cols;
    g.rows = (n + g.cols - 1) / g.cols;
    g.card_w = (w - (g.cols - 1) * g.gap) / g.cols;

    if (max_slots < 1) {                                     /* live: a 5px bar */
        g.body_h = N_LABEL_GAP + N_BAR_SM;
        g.item_h = dmax(N_ROOM_MIN_H, 2*N_ROOM_PAD + node_head_h(&g) + g.body_h);
        g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
        return g;
    }
    g.slot_h = N_PAD;
    g.body_h = max_slots * g.slot_h + (max_slots - 1)*N_BORDER;
    g.item_h = 2*N_ROOM_PAD + node_head_h(&g) + g.body_h;
    g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
    if (g.total_h > budget_h) {                              /* many rows: shrink slots to fit */
        double avail = (budget_h - (g.rows - 1) * g.gap) / g.rows - 2*N_ROOM_PAD - node_head_h(&g);
        g.slot_h = floor((avail - (max_slots - 1)*N_BORDER) / max_slots);
        if (g.slot_h < N_ACTION_GAP) g.slot_h = N_ACTION_GAP;
        g.body_h = max_slots * g.slot_h + (max_slots - 1)*N_BORDER;
        g.item_h = 2*N_ROOM_PAD + node_head_h(&g) + g.body_h;
        g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
    }
    return g;
}

/* Any present disk of the machine that is not green (#872: fail or failed_last)? */
static int room_machine_has_warn(const Machine *m) {
    for (int i = 0; i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present && smart_level(m->room_drawers[i].smart)) return 1;
    return 0;
}

/* The node's box, its .top row (name, dot) and small line; returns the
 * content rect below them. <name_hit> registers the name as a button. */
static Rect room_node_chrome(App *a, cairo_t *cr, Rect item, const RoomGrid *g,
                             const char *name, int name_fill, int name_hit,
                             Rgb dot, Rgb line, Text *small) {
    const Theme *t = a->theme;
    draw_fill_rrect(cr, item, RADIUS_SM, t->disk_bg);
    draw_border_rrect(cr, item, RADIUS_SM, line, N_BORDER);
    double x = item.x + N_ROOM_PAD, y = item.y + N_ROOM_PAD, w = item.w - 2*N_ROOM_PAD;
    /* .top{display:flex;justify-content:space-between}: name at the start
     * (right), the dot at the end (left) */
    Text nt = text_make(cr, FONT_SANS, N_NODE_TITLE, 600, name, 0, DIR_RTL);
    double pill_w = fmin(nt.w + N_ACTION_GAP, w - N_NODE_DOT - N_ACTION_GAP);
    Rect pill = { x + w - pill_w, y, pill_w, g->name_h };
    if (name_hit) {
        draw_fill_rrect(cr, pill, RADIUS_SM, name_fill ? t->indigo : t->field);
        draw_border_rrect(cr, pill, RADIUS_SM, name_fill ? t->indigo : t->hair, 1);
        hit_add(a, pill, name_hit);
    }
    cairo_save(cr);
    cairo_rectangle(cr, pill.x, pill.y, pill.w, pill.h); cairo_clip(cr);
    text_draw_r(cr, &nt, pill.x + pill.w - N_ACTION_GAP/2, y + (g->name_h - nt.h) / 2,
                name_fill ? t->on_ink : t->ink);
    cairo_restore(cr);
    text_free(&nt);
    cairo_arc(cr, x + N_NODE_DOT/2, y + g->name_h/2, N_NODE_DOT/2, 0, 2*M_PI);
    draw_set(cr, dot); cairo_fill(cr);
    y += g->name_h + 2;
    text_draw(cr, small, x, y, t->muted);
    y += g->small_h + N_LABEL_GAP;
    return (Rect){ x, y, w, item.y + item.h - N_ROOM_PAD - y };
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
    /* The name is a button (#714): click it to select/clear every disk of the
     * machine. It fills indigo when they are all selected, so the state shows. */
    int all_sel = m->awake && room_machine_all_selected(a, m);
    int warn = m->awake && room_machine_has_warn(m);
    char st[64];
    if (m->awake) snprintf(st, sizeof st, "ער · %d מגירות מוכנות", m->fresh_drawers);
    else          snprintf(st, sizeof st, "כבוי");
    Text small = text_make(cr, FONT_SANS, N_NODE_SUB, 400, st, (int)(item.w - 2*N_ROOM_PAD), DIR_RTL);
    Rect card = room_node_chrome(a, cr, item, g, name, all_sel,
                                 m->awake ? HIT_ROOM_MACHINE_BASE + mi : 0,
                                 m->awake ? (warn ? t->warn : t->led_ok) : t->hair,
                                 !m->awake ? t->disk_line : warn ? t->node_warn_line : t->node_active_line,
                                 &small);
    text_free(&small);

    int slots = m->drawer_count;
    if (slots < 1) slots = 1;
    if (slots > MAX_DRAWERS) slots = MAX_DRAWERS;

    double sx = card.x, sy = card.y, sw = card.w;
    for (int port = 1; port <= slots; port++) {
        const RoomDrawer *d = room_drawer_at(m, port);
        Rect slot = { sx, sy + (port - 1) * (g->slot_h + 1), sw, g->slot_h };
        int selected = d && room_selection_override(a, m, port, d->selected);
        Rgb fill = t->led_idle;
        if (m->awake && d)
            fill = smart_color(t, d->smart);

        draw_fill_rrect(cr, slot, RADIUS_SM, fill);
        draw_border_rrect(cr, slot, RADIUS_SM, selected ? t->indigo : t->hair,
                          selected ? 2 : 1);

        if (m->awake && d) {
            char capacity[24];
            fmt_bytes(capacity, sizeof capacity, (double)d->size_bytes);
            Text ct = text_make(cr, FONT_SANS, g->slot_h >= N_CLONE_TITLE ? N_HEADER_FONT : N_IMAGE_SUB,
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

/* The live round as .room-node tiles: name + dot, the status line of
 * room.js machineRows (as Pango markup, colours kept), and a .native-bar with
 * the machine's progress -- yellow for a partial ending, red for failed. */
static void room_live_grid_draw(App *a, cairo_t *cr, double x, double y, double w,
                                const RoomGrid *g, int wave_open) {
    const Theme *t = a->theme;
    if (a->st.nmachines == 0) {
        Text empty = sub_make(cr, "אין מחשבי שיכפול רשומים — רושמים אותם בקונסולה, במסך המחשבים.", w);
        text_draw_r(cr, &empty, x + w, y, t->muted);
        text_free(&empty);
        return;
    }
    for (int i = 0; i < a->st.nmachines; i++) {
        const Machine *m = &a->st.machines[i];
        int row = i / g->cols, col = i % g->cols;
        Rect item = { x + w - g->card_w - col * (g->card_w + g->gap), y + row * (g->item_h + g->gap),
                      g->card_w, g->item_h };
        int on = m->awake || m->joined;
        int failed = !strcmp(m->state, "failed"), partial = !strcmp(m->state, "partial");
        char mk[1024];
        room_status_markup(a, m, wave_open ? ROWS_ROOM_SETUP : ROWS_ROOM_LIVE, mk, sizeof mk);
        Text small = text_make_markup(cr, FONT_SANS, N_NODE_SUB, 400, mk, (int)(item.w - 2*N_ROOM_PAD), DIR_RTL);
        if (!on) cairo_push_group(cr);                       /* .room-row.dim{opacity:.55} */
        Rect body = room_node_chrome(a, cr, item, g, m->name[0] ? m->name : m->mac, 0, 0,
                                     failed ? ROOM_BAD : partial ? t->warn : on ? t->led_ok : t->hair,
                                     failed ? t->danger_line : partial ? t->node_warn_line : on ? t->node_active_line : t->disk_line,
                                     &small);
        text_free(&small);
        int pct = m->pct >= 0 ? m->pct : (!strcmp(m->state, "done") ? 100 : 0);
        draw_thin_bar(cr, t, (Rect){ body.x, body.y + N_LABEL_GAP, body.w, N_BAR_SM }, pct,
                      failed ? ROOM_BAD : partial ? t->warn : t->led_write);
        if (!on) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, N_DIM_ALPHA); }
    }
}

void screen_room(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 2 * N_PAD, in_h = field_height(cr), btn_h = btn_height(cr);
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
        RoomGrid grid = room_grid_make(cr, inner, s->nmachines, max_slots, (H-head_h-N_STATUS_H)/2);
        Text err = error_make(cr, s->room_error, inner);
        double err_h = dmax(err.h, ERROR_MIN_H);
        double grid_h = s->nmachines ? grid.total_h : text_line_height(cr, FONT_SANS, N_SUB);
        double sel_all_h = s->nmachines ? btn_h : 0;
        double ready_line_h = dmax(ready_t.h, sel_all_h);
        double fields_h = direct ? l1.h + N_FORM_GAP : l1.h + 2 + in_h + N_FORM_GAP + l2.h + 2 + in_h + N_FORM_GAP;
        double body_h = N_PAD + fields_h + ready_line_h + N_ACTION_GAP + grid_h + N_LABEL_GAP + err_h + N_PAD;
        Text labels[3] = { btn_label(cr, direct ? "פתח סבב והער את המחשבים שנבחרו" : "פתח סבב והער את החדר"), btn_label(cr, "העֵר את מחשבי השיכפול"), btn_label(cr, "חזרה") };
        static const int kinds[3] = { BTN_PRIMARY, BTN_PLAIN, BTN_PLAIN };
        static const int ids[3] = { HIT_ROOM_OPEN, HIT_ROOM_WAKE, HIT_BACK };

        Rect body, foot;
        card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
        body_clip_begin(a, cr, body);
        double x = body.x + N_PAD, y = body.y + N_PAD;
        const char *opts[MAX_IMAGES + 1]; char obuf[MAX_IMAGES][168];
        int nopt = image_options(a, opts, obuf, MAX_IMAGES + 1);
        Rect sel_r = { x, y + l1.h + 2, inner, in_h };
        if (direct) {
            text_draw_r(cr, &l1, x + inner, y, t->ink);  y += l1.h + N_FORM_GAP;
        } else {
            text_draw(cr, &l1, x, y, t->muted);              y += l1.h + 2;
            draw_select(a, cr, sel_r, opts[a->image_sel < nopt ? a->image_sel : 0], HIT_ROOM_IMAGE);
            y += in_h + N_FORM_GAP;
            text_draw(cr, &l2, x, y, t->muted);              y += l2.h + 2;
            draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->room_target, 0, NULL, HIT_ROOM_TARGET);
            y += in_h + N_FORM_GAP;
        }
        /* ready count on the right; a select/clear-all button on the left (#714) */
        if (s->nmachines) {
            Text sa = btn_label(cr, "בחר / נקה הכל");
            double saw = btn_width(&sa);
            draw_btn(a, cr, (Rect){ x, y, saw, btn_h }, &sa, BTN_PLAIN, HIT_ROOM_SELECT_ALL);
            text_free(&sa);
        }
        text_draw_r(cr, &ready_t, x + inner, y + (ready_line_h - ready_t.h) / 2, t->ink);
        y += ready_line_h + N_ACTION_GAP;
        room_grid_draw(a, cr, x, y, inner, &grid);       y += grid_h + N_LABEL_GAP;
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
    char sub[240], count[48], hint[520], pace[160], age[64];
    /* #410: how old this picture is, next to the wave -- a state file that
     * stopped changing shows a growing number, not a current-looking screen. */
    pace_age(s->updated, age, sizeof age);
    snprintf(sub, sizeof sub, "משדר: %s · גל %d%s%s", s->round_image, s->wave_number,
             age[0] ? " · " : "", age);
    snprintf(count, sizeof count, "%d / %d", s->written, s->target);
    if (s->wave_open) {
        int need = s->remaining - s->ready; if (need < 0) need = 0;
        snprintf(hint, sizeof hint, "הגל ממתין: %d מגירות מוכנות, צריך עוד %d — או \"התחל עכשיו\". החלפתם מגירות? הדליקו את המכונות והן יצטרפו.",
                 s->ready, need);
    } else {
        /* #410: elapsed, rate and ETA come from the server (round= record);
         * "לא נמדד" in words when it has nothing yet -- never a 0 rate. */
        pace_text(s->elapsed_s, s->rate_bps, s->eta_s, 0, pace, sizeof pace);
        snprintf(hint, sizeof hint, "הגל משדר — %s. מכונה שסיימה — מכבים, מחליפים מגירות, מדליקים.",
                 pace[0] ? pace : "זמן וקצב טרם נמדדו");
    }
    int pct = s->target > 0 ? (int)floor(100.0 * s->written / s->target + 0.5) : 0;

    Head hd = head_make(cr, "הפצה למחשבי שיכפול", sub, inner);
    ProgLine pl = progline_make(cr, count, "כוננים שנכתבו", inner);
    Text hint_t = alert_make(cr, hint, inner);                  /* .native-alert */
    RoomGrid grid = room_grid_make(cr, inner, s->nmachines, 0, (H-head_h-N_STATUS_H)/2);
    double rows_h = s->nmachines ? grid.total_h : text_line_height(cr, FONT_SANS, N_SUB);
    Text cl = { NULL, 0, 0 };
    double confirm_h = 0;
    if (a->room_confirming) {
        cl = confirm_label(cr, s->round_image, 1, inner);
        confirm_h = N_LABEL_GAP + cl.h + 2 + in_h + N_FORM_GAP;             /* label margin-top 6; input inside it */
    }
    Text err = error_make(cr, s->room_error, inner);
    double err_h = dmax(err.h, ERROR_MIN_H);
    double body_h = N_PAD + pl.h + N_LABEL_GAP + N_BAR_H + N_ACTION_GAP + alert_height(&hint_t) + N_CHOICE_GAP + rows_h + confirm_h + N_LABEL_GAP + err_h + N_PAD;
    Text labels[4]; int kinds[4], ids[4], nb = 0;
    if (s->wave_open) { labels[nb] = btn_label(cr, "התחל עכשיו"); kinds[nb] = BTN_PRIMARY; ids[nb++] = HIT_ROOM_START; }
    labels[nb] = btn_label(cr, "העֵר שוב");  kinds[nb] = BTN_PLAIN;  ids[nb++] = HIT_ROOM_WAKE;
    labels[nb] = btn_label(cr, "עצור סבב");  kinds[nb] = BTN_DANGER; ids[nb++] = HIT_ROOM_CLOSE;
    labels[nb] = btn_label(cr, "חזרה");      kinds[nb] = BTN_PLAIN;  ids[nb++] = HIT_BACK;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + N_PAD, y = body.y + N_PAD;
    progline_draw(cr, t, &pl, x, y, inner);              y += pl.h + N_LABEL_GAP;
    draw_big_bar(cr, t, (Rect){ x, y, inner, N_BAR_H }, pct, 0); y += N_BAR_H + N_ACTION_GAP;
    draw_alert(cr, t, (Rect){ x, y, inner, alert_height(&hint_t) }, &hint_t); y += alert_height(&hint_t) + N_CHOICE_GAP;
    room_live_grid_draw(a, cr, x, y, inner, &grid, s->wave_open); y += rows_h;
    if (a->room_confirming) {
        y += N_LABEL_GAP;
        text_draw(cr, &cl, x, y, t->muted);              y += cl.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->room_confirm, 0, NULL, HIT_ROOM_CONFIRM);
        y += in_h + N_FORM_GAP;
        text_free(&cl);
    }
    y += N_LABEL_GAP;
    text_draw(cr, &err, x, y, t->danger);
    body_clip_end(a, cr);
    foot_draw_bg(cr, t, foot);
    foot_buttons(a, cr, foot, labels, kinds, ids, nb);
    card_end(a, cr);
    text_free(&hint_t); text_free(&err);
}

