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

/* Owner, 10/10: a selected slot wears a 3px action ring 2px outside it, so
 * the slots stand SLOT_GAP apart and the stack has SLOT_RING of room above
 * and below -- rings never overlap a neighbour or the status line. */
#define SLOT_RING 5.0           /* 2px gap + 3px ring */
#define SLOT_GAP  (2 * SLOT_RING)

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
                               int wanted_cols, double budget_h) {
    RoomGrid g;
    memset(&g, 0, sizeof g);
    g.gap = N_ROOM_GAP;
    g.name_h = dmax(text_line_height(cr, FONT_SANS, N_NODE_TITLE), N_NODE_DOT);
    g.small_h = text_line_height(cr, FONT_SANS, N_NODE_SUB);
    if (n <= 0) return g;
    if (max_slots > MAX_DRAWERS) max_slots = MAX_DRAWERS;

    g.cols = n < wanted_cols ? n : wanted_cols;
    g.rows = (n + g.cols - 1) / g.cols;
    g.card_w = (w - (g.cols - 1) * g.gap) / g.cols;

    if (max_slots < 1) {                                     /* live: a 5px bar */
        g.body_h = N_LABEL_GAP + N_BAR_SM;
        g.item_h = dmax(N_ROOM_MIN_H, 2*N_ROOM_PAD + node_head_h(&g) + g.body_h);
        g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
        return g;
    }
    g.slot_h = N_PAD;
    g.body_h = max_slots * g.slot_h + (max_slots - 1)*SLOT_GAP + 2*SLOT_RING;
    g.item_h = 2*N_ROOM_PAD + node_head_h(&g) + g.body_h;
    g.total_h = g.rows * g.item_h + (g.rows - 1) * g.gap;
    if (g.total_h > budget_h) {                              /* many rows: shrink slots to fit */
        double avail = (budget_h - (g.rows - 1) * g.gap) / g.rows - 2*N_ROOM_PAD - node_head_h(&g);
        g.slot_h = floor((avail - (max_slots - 1)*SLOT_GAP - 2*SLOT_RING) / max_slots);
        if (g.slot_h < N_ACTION_GAP) g.slot_h = N_ACTION_GAP;
        g.body_h = max_slots * g.slot_h + (max_slots - 1)*SLOT_GAP + 2*SLOT_RING;
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
    int nsel = 0;                                  /* owner, 10/10: say how many are picked */
    for (int i = 0; m->awake && i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present && room_selection_override(a, m, m->room_drawers[i].port, m->room_drawers[i].selected)) nsel++;
    char st[96];
    if (m->awake && nsel) snprintf(st, sizeof st, "ער · %d מגירות מוכנות · %d נבחרו", m->fresh_drawers, nsel);
    else if (m->awake)    snprintf(st, sizeof st, "ער · %d מגירות מוכנות", m->fresh_drawers);
    else                  snprintf(st, sizeof st, "כבוי");
    Text small = text_make(cr, FONT_SANS, N_NODE_SUB, 400, st, (int)(item.w - 2*N_ROOM_PAD), DIR_RTL);
    Rect card = room_node_chrome(a, cr, item, g, name, all_sel,
                                 m->awake ? HIT_ROOM_MACHINE_BASE + mi : 0,
                                 m->awake ? (warn ? t->warn : t->led_ok) : t->hair,
                                 !m->awake ? t->disk_line : warn ? t->node_warn_line : t->node_active_line,
                                 &small);
    text_free(&small);
    if (nsel) draw_border_rrect(cr, item, RADIUS_SM, t->indigo, 2);   /* a machine with picked disks */

    int slots = m->drawer_count;
    if (slots < 1) slots = 1;
    if (slots > MAX_DRAWERS) slots = MAX_DRAWERS;

    double sx = card.x, sy = card.y + SLOT_RING, sw = card.w;
    for (int port = 1; port <= slots; port++) {
        const RoomDrawer *d = room_drawer_at(m, port);
        Rect slot = { sx, sy + (port - 1) * (g->slot_h + SLOT_GAP), sw, g->slot_h };
        int selected = d && room_selection_override(a, m, port, d->selected);
        int avail = m->awake && d;
        /* owner, 10/10: picked vs not must read at a glance. Picked = the
         * SMART colour solid, a 3px action ring 2px outside and a check at
         * the start. Available but not picked = the same colour lightened
         * (45% surface) inside a border of the colour itself -- the SMART
         * meaning (green ok / orange warn / red failed_last) stays. */
        Rgb smart = avail ? smart_color(t, d->smart) : t->led_idle;
        Rgb light = { smart.r * .55 + t->surface.r * .45, smart.g * .55 + t->surface.g * .45,
                      smart.b * .55 + t->surface.b * .45 };
        if (avail && selected) {
            draw_fill_rrect(cr, slot, RADIUS_SM, smart);
            Rect ring = { slot.x - SLOT_RING, slot.y - SLOT_RING, slot.w + 2 * SLOT_RING, slot.h + 2 * SLOT_RING };
            draw_border_rrect(cr, ring, RADIUS_SM + SLOT_RING, t->indigo, 3);
        } else if (avail) {
            draw_fill_rrect(cr, slot, RADIUS_SM, light);
            draw_border_rrect(cr, slot, RADIUS_SM, smart, 2);
        } else {
            draw_fill_rrect(cr, slot, RADIUS_SM, t->led_idle);
            draw_border_rrect(cr, slot, RADIUS_SM, t->hair, 1);
        }

        if (avail) {
            char capacity[24];
            fmt_bytes(capacity, sizeof capacity, (double)d->size_bytes);
            Text ct = text_make(cr, FONT_SANS, g->slot_h >= N_CLONE_TITLE ? N_HEADER_FONT : N_IMAGE_SUB,
                                selected ? 700 : 600, capacity, 0, DIR_LTR);
            cairo_save(cr);
            cairo_rectangle(cr, slot.x + 2, slot.y + 1, slot.w - 4, slot.h - 2);
            cairo_clip(cr);
            text_draw(cr, &ct, slot.x + (slot.w - ct.w) / 2,
                      slot.y + (slot.h - ct.h) / 2, selected ? t->on_ink : t->ink);
            if (selected && slot.h >= 12) {          /* the check at the slot's start (right, RTL) */
                double h = fmin(slot.h * .5, 14), cx = slot.x + slot.w - 8 - h, cy = slot.y + (slot.h - h) / 2;
                cairo_move_to(cr, cx + h * .12, cy + h * .52);
                cairo_line_to(cr, cx + h * .40, cy + h * .80);
                cairo_line_to(cr, cx + h * .90, cy + h * .20);
                cairo_set_line_width(cr, 2.2);
                cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
                draw_set(cr, t->on_ink); cairo_stroke(cr);
            }
            cairo_restore(cr);
            text_free(&ct);
        }

        /* Empty slots and every slot on an off machine are display-only. */
        if (m->awake && d)
            hit_add(a, slot, HIT_ROOM_DISK_BASE + mi * MAX_DRAWERS + (port - 1));
        /* #1269: a chosen disk's drivers tag in pick mode -- after the slot, so its hit wins */
        if (m->awake && d && selected) room_drv_tag_draw(a, cr, slot, m, mi, port);
    }
}

/* #1235: a drawer with no serial (fresh null, #105). The round counts by
 * serial, so it cannot tell whether that drive was written: say so by slot
 * instead of drawing it as one more drawer (rule 5). One line per drawer;
 * when they do not all fit, the last line says that more were left out --
 * never a silent cut. Text{NULL} when every drawer is identified. */
static Text room_unidentified_make(cairo_t *cr, const State *s, double w) {
    char buf[2048], *line;
    const char *more = "\nועוד כוננים שלא ניתן לזהות — ראו בקונסולה";
    size_t used = 0, cap = sizeof buf - strlen(more) - 1;
    buf[0] = 0;
    for (int i = 0; i < s->nmachines; i++) {
        const Machine *m = &s->machines[i];
        for (int j = 0; j < m->nroom_drawers; j++) {
            const RoomDrawer *d = &m->room_drawers[j];
            if (!d->present || !d->unidentified) continue;
            line = buf + used;
            int k = snprintf(line, cap - used, "%sלא ניתן לזהות את הכונן בחריץ %d · %s",
                             used ? "\n" : "", d->port, m->name[0] ? m->name : m->mac);
            if (k < 0 || (size_t)k >= cap - used) {
                snprintf(line, sizeof buf - used, "%s", more);
                return text_make(cr, FONT_SANS, N_SUB, 600, buf, (int)w, DIR_RTL);
            }
            used += (size_t)k;
        }
    }
    if (!used) return (Text){ NULL, 0, 0 };
    return text_make(cr, FONT_SANS, N_SUB, 600, buf, (int)w, DIR_RTL);
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

/* The live round as .room-node tiles.  The protocol has one progress value
 * per machine and SMART per physical drawer, so every drawer gets its own
 * labelled bar with the machine progress and its own health colour. */
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
                  failed ? t->danger : partial ? t->warn : on ? t->led_ok : t->hair,
                                     failed ? t->danger : partial ? t->node_warn_line : on ? t->node_active_line : t->disk_line,
                                     &small);
        text_free(&small);
        int pct = m->pct >= 0 ? m->pct : (!strcmp(m->state, "done") ? 100 : 0);
        /* mockup 10/10: a bar per disk the machine REPORTED (present
         * drawers, by port), not per configured slot -- a slot with no disk
         * was drawn as "דיסק 3 —" with the machine's progress on it. */
        const RoomDrawer *found[MAX_DRAWERS]; int nfound = 0;
        for (int port = 1; port <= MAX_DRAWERS; port++) {
            const RoomDrawer *d = room_drawer_at(m, port);
            if (d) found[nfound++] = d;
        }
        int slots = nfound ? nfound : 1;
        double slot_h = body.h / slots;
        for (int k = 0; k < slots; k++) {
            const RoomDrawer *d = nfound ? found[k] : NULL;
            char size[32] = "—", label[80], percent[16];
            if (d && d->size_bytes) fmt_bytes(size, sizeof size, (double)d->size_bytes);
            if (d) snprintf(label, sizeof label, "דיסק %d  %s", d->port, size);
            else   snprintf(label, sizeof label, "%s", "לא דווחו דיסקים");
            snprintf(percent, sizeof percent, "%d%%", pct);
            Text lt = rtl_block_make(cr, 11, 500, label, body.w - 48);
            Text pt = text_make(cr, FONT_MONO, 11, 500, percent, 0, DIR_LTR);
            double sy = body.y + k * slot_h;
            text_draw_r(cr, &lt, body.x + body.w, sy, t->ink);
            text_draw(cr, &pt, body.x, sy, t->muted);
            Rgb bar = failed ? t->danger : partial ? t->warn :
                      d ? smart_color(t, d->smart) : t->bar_done;
            draw_thin_bar(cr, t, (Rect){body.x, sy + 15, body.w, N_BAR_SM}, pct, bar);
            text_free(&lt); text_free(&pt);
        }
        if (!on) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, N_DIM_ALPHA); }
    }
}

/* mockup 10/10 ("חדר + סבב חי"): "עצור סבב…" stands apart at the start as
 * red words; it opens the #1270 confirm dialog (the red "עצור סבב" is
 * there). The rest sit at the inline end, the primary last. */
static void live_footer(App *a, cairo_t *cr, Rect foot) {
    const Theme *t = a->theme; const State *s = &a->st;
    double bh = btn_height(cr), by = foot.y + N_ACTION_TOP;
    Text stop = btn_label(cr, "עצור סבב…");
    double sw = btn_width(&stop), sx = foot.x + foot.w - N_PAD - sw;
    draw_btn(a, cr, (Rect){ sx, by, sw, bh }, &stop, BTN_GHOST_DANGER, HIT_ROOM_CLOSE);
    text_free(&stop);
    Text labels[3]; int kinds[3], ids[3], nb = 0;     /* from the inline end: the primary first */
    if (s->wave_open) { labels[nb] = btn_label(cr, "התחל עכשיו"); kinds[nb] = BTN_PRIMARY; ids[nb++] = HIT_ROOM_START; }
    labels[nb] = btn_label(cr, "העֵר שוב"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_ROOM_WAKE;
    labels[nb] = btn_label(cr, "חזרה");     kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_BACK;
    double x = foot.x + N_PAD;
    for (int i = 0; i < nb; i++) {
        double bw = btn_width(&labels[i]);
        draw_btn(a, cr, (Rect){ x, by, bw, bh }, &labels[i], kinds[i], ids[i]);
        x += bw + 8;
        text_free(&labels[i]);
    }
    double room = sx - 12 - (x + 4);
    if (room > 80) {
        Text h = rtl_block_make(cr, 12, 400, "עצירה באמצע — אחרי אישור בחלון", room);
        text_draw_r(cr, &h, sx - 12, by + (bh - h.h) / 2, t->muted);
        text_free(&h);
    }
}

void screen_room(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 2 * N_PAD, in_h = field_height(cr), btn_h = btn_height(cr);
    int ready = 0;
    int room_cols = H / W >= .7 ? 4 : 6;
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
        RoomGrid grid = room_grid_make(cr, inner, s->nmachines, max_slots,
                                       room_cols, (H-head_h-N_STATUS_H)/2);
        Text err = error_make(cr, s->room_error, inner);
        double err_h = dmax(err.h, ERROR_MIN_H);
        Text unid = room_unidentified_make(cr, s, inner);
        double unid_h = unid.layout ? unid.h + N_LABEL_GAP : 0;
        double grid_h = s->nmachines ? grid.total_h : text_line_height(cr, FONT_SANS, N_SUB);
        double sel_all_h = s->nmachines ? btn_h : 0;
        double ready_line_h = dmax(ready_t.h, sel_all_h);
        double fields_h = direct ? l1.h + N_FORM_GAP : l1.h + 2 + in_h + N_FORM_GAP + l2.h + 2 + in_h + N_FORM_GAP;
        /* #1269: "להוסיף דרייברים" under the fields, its summary under the grid */
        double drv_h = room_drv_block_height(a, cr, inner);
        Text drv_sum = room_drv_summary_make(a, cr, inner);
        double drv_sum_h = room_drv_summary_height(&drv_sum);
        double body_h = N_PAD + fields_h + drv_h + ready_line_h + N_ACTION_GAP + grid_h + N_LABEL_GAP + drv_sum_h + unid_h + err_h + N_PAD;
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
        Rect drv_sel = room_drv_block_draw(a, cr, x, y, inner);   y += drv_h;   /* #1269 */
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
        room_drv_summary_draw(a, cr, x, y, inner, &drv_sum);   y += drv_sum_h;
        if (unid.layout) { text_draw_r(cr, &unid, x + inner, y, t->warning_ink); y += unid_h; }
        text_draw(cr, &err, x, y, t->danger_fg);
        body_clip_end(a, cr);
        foot_draw_bg(cr, t, foot);
        foot_buttons(a, cr, foot, labels, kinds, ids, 3);
        card_end(a, cr);
        if (!direct && a->dd_open == HIT_ROOM_IMAGE) draw_popup(a, cr, sel_r, opts, nopt, a->image_sel);   /* outside the card's clip, topmost */
        if (a->dd_open == HIT_ROOM_DRV_MODEL && drv_sel.w > 0) {                                           /* #1269 */
            const char *dopts[MAX_DRIVER_MODELS]; char dbuf[MAX_DRIVER_MODELS][200];
            int dn = room_drv_options(a, dopts, dbuf, MAX_DRIVER_MODELS), dsel = -1;
            for (int i = 0; i < s->ndriver_models; i++)
                if (!strcmp(s->driver_models[i].vendor, a->drv_vendor) && !strcmp(s->driver_models[i].model, a->drv_model)) dsel = i;
            draw_popup(a, cr, drv_sel, dopts, dn, dsel);
        }
        text_free(&l1); text_free(&l2); text_free(&ready_t); text_free(&err); text_free(&unid); text_free(&drv_sum);
        return;
    }

    /* ---- renderLive ---- */
    char sub[300], count[48], hint[520], pace[160], age[64], image[112];
    /* #410: how old this picture is, next to the wave -- a state file that
     * stopped changing shows a growing number, not a current-looking screen. */
    pace_age(s->updated, age, sizeof age);
    bidi_isolate(s->round_image, image, sizeof image);   /* a Latin-first name stays one run */
    snprintf(sub, sizeof sub, RLM "משדר: %s · גל %d%s%s", image, s->wave_number,
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
    int live_slots = 1;                 /* the deepest machine, in disks it reported */
    for (int i = 0; i < s->nmachines; i++) {
        int found = 0;
        for (int j = 0; j < s->machines[i].nroom_drawers; j++) found += s->machines[i].room_drawers[j].present;
        if (found > live_slots) live_slots = found;
    }
    RoomGrid grid = room_grid_make(cr, inner, s->nmachines, live_slots,
                                   room_cols, (H-head_h-N_STATUS_H)/2);
    double rows_h = s->nmachines ? grid.total_h : text_line_height(cr, FONT_SANS, N_SUB);
    Text err = error_make(cr, s->room_error, inner);
    double err_h = dmax(err.h, ERROR_MIN_H);
    Text unid = room_unidentified_make(cr, s, inner);
    double unid_h = unid.layout ? N_LABEL_GAP + unid.h : 0;
    /* #1269: what happened to each drawer's drivers (drawer_list[].drivers) */
    int drv_failed = 0;
    Text drv_res = room_drv_results_make(cr, s, inner, &drv_failed);
    double drv_res_h = drv_res.layout ? N_LABEL_GAP + drv_res.h : 0;
    double body_h = N_PAD + pl.h + N_LABEL_GAP + N_BAR_H + N_ACTION_GAP + alert_height(&hint_t) + N_CHOICE_GAP + rows_h + unid_h + drv_res_h + N_LABEL_GAP + err_h + N_PAD;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + N_PAD, y = body.y + N_PAD;
    text_draw_r(cr, &pl.big, x + inner, y, t->ink);
    text_draw_r(cr, &pl.small, x + inner - pl.big.w - 10, y + pl.drop, t->ink);
    text_free(&pl.big); text_free(&pl.small);             y += pl.h + N_LABEL_GAP;
    draw_big_bar(cr, t, (Rect){ x, y, inner, N_BAR_H }, pct, 0); y += N_BAR_H + N_ACTION_GAP;
    draw_alert(cr, t, (Rect){ x, y, inner, alert_height(&hint_t) }, &hint_t); y += alert_height(&hint_t) + N_CHOICE_GAP;
    room_live_grid_draw(a, cr, x, y, inner, &grid, s->wave_open); y += rows_h;
    if (unid.layout) { text_draw_r(cr, &unid, x + inner, y + N_LABEL_GAP, t->warning_ink); y += unid_h; }
    if (drv_res.layout) {           /* failed = done without its drivers: orange, not red (#856) */
        text_draw_r(cr, &drv_res, x + inner, y + N_LABEL_GAP, drv_failed ? t->warning_ink : t->ink);
        y += drv_res_h;
    }
    y += N_LABEL_GAP;
    text_draw(cr, &err, x, y, t->danger_fg);
    body_clip_end(a, cr);
    foot_draw_bg(cr, t, foot);
    live_footer(a, cr, foot);
    card_end(a, cr);
    text_free(&hint_t); text_free(&err); text_free(&unid); text_free(&drv_res);
}
