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

static const RoomDrawer *room_drawer_row(const Machine *m, int row, int *index) {
    int mapped_rows = m->nrslots > 0 ? m->nrslots : m->drawer_count;
    if (mapped_rows < 1) mapped_rows = 1;
    if (mapped_rows > MAX_DRAWERS) mapped_rows = MAX_DRAWERS;
    if (row < mapped_rows) {
        for (int i = 0; i < m->nroom_drawers; i++)
            if (m->room_drawers[i].present &&
                eff_drawer(m->room_drawers[i].drawer, m->room_drawers[i].port) == row + 1) {
                *index = i; return &m->room_drawers[i];
            }
        *index = -1; return NULL;
    }
    int wanted = row - mapped_rows;
    for (int i = 0; i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present &&
            eff_drawer(m->room_drawers[i].drawer, m->room_drawers[i].port) == 0) {
            if (wanted-- == 0) { *index = i; return &m->room_drawers[i]; }
        }
    *index = -1; return NULL;
}

static int room_drawer_after(const RoomDrawer *a, const RoomDrawer *b) {
    int ad = eff_drawer(a->drawer, a->port), bd = eff_drawer(b->drawer, b->port);
    if (ad && bd) return ad > bd;
    if (ad != 0) return 0;
    if (bd != 0) return 1;
    return a->port > b->port;
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

/* Any present disk of the machine that is not green (#872: fail or failed_last;
 * #432: a link slower than its port)? */
static int room_machine_has_warn(const Machine *m) {
    for (int i = 0; i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present && (smart_level(m->room_drawers[i].smart) || m->room_drawers[i].link_slow
                                           || m->room_drawers[i].crc_suspect)) return 1;   /* #1366 */
    return 0;
}

/* #432: the slot's colour -- SMART first (red wins), then a slow link is
 * orange like a SMART warning: it asks, it does not block. */
static Rgb room_drawer_color(const Theme *t, const RoomDrawer *d) {
    if (!smart_level(d->smart) && (d->link_slow || d->crc_suspect)) return t->warn;   /* #1366: CRC over the threshold */
    return smart_color(t, d->smart);
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
        if (!room_selectable(d)) continue;
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
        if (room_selectable(&m->room_drawers[i]) && room_selection_override(a, m, m->room_drawers[i].port, m->room_drawers[i].selected)) nsel++;
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

    int slots = room_rows(m);
    int mapped_rows = m->nrslots > 0 ? m->nrslots : m->drawer_count;
    if (mapped_rows < 1) mapped_rows = 1;
    if (mapped_rows > MAX_DRAWERS) mapped_rows = MAX_DRAWERS;

    double sx = card.x, sy = card.y + SLOT_RING, sw = card.w;
    for (int row = 0; row < slots; row++) {
        int j = -1, drawer = row + 1, port = drawer;
        const RoomDrawer *d = room_drawer_row(m, row, &j);
        int mapped = row < mapped_rows;
        Rect slot = { sx, sy + row * (g->slot_h + SLOT_GAP), sw, g->slot_h };
        int selected = d && room_selectable(d) && room_selection_override(a, m, d->port, d->selected);
        int avail = m->awake && d && room_selectable(d);
        /* owner, 10/10: picked vs not must read at a glance. Picked = the
         * SMART colour solid, a 3px action ring 2px outside and a check at
         * the start. Available but not picked = the same colour lightened
         * (45% surface) inside a border of the colour itself -- the SMART
         * meaning (green ok / orange warn / red failed_last) stays. */
        Rgb smart = avail ? room_drawer_color(t, d) : t->led_idle;
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

        /* #704: the operator's name of the slot -- "name · capacity" on a disk,
         * the name alone (muted) on an empty or unreachable slot. No name =
         * the capacity alone, exactly as before. */
        const char *sname = "";
        if (mapped && m->nrslots > 0) sname = m->rslots[drawer - 1].name;
        else if (mapped) sname = m->slot_names[port - 1];
        const char *dname = d && d->slot_name[0] ? d->slot_name : sname;
        if (!avail && mapped && sname[0] && !(d && m->awake)) {
            Text nt = text_make(cr, FONT_SANS, g->slot_h >= N_CLONE_TITLE ? N_HEADER_FONT : N_IMAGE_SUB,
                                500, sname, 0, DIR_RTL);
            cairo_save(cr);
            cairo_rectangle(cr, slot.x + 2, slot.y + 1, slot.w - 4, slot.h - 2);
            cairo_clip(cr);
            text_draw(cr, &nt, slot.x + (slot.w - nt.w) / 2, slot.y + (slot.h - nt.h) / 2, t->muted);
            cairo_restore(cr);
            text_free(&nt);
        }
        /* #1495: a mapped row says "name · capacity" or the capacity alone, as
         * before (the row is the number); a row not in the mapping says
         * "SATA n (לא ממופה)" -- it cannot be picked. */
        if (d && (m->awake || !mapped)) {
            char capacity[32] = "", named[SLOT_NAME_MAX + 128], label[SLOT_NAME_MAX + 64];
            fmt_bytes(capacity, sizeof capacity, (double)d->size_bytes);
            room_drawer_label(m, d, label, sizeof label);
            if (!mapped) snprintf(named, sizeof named, "%s", label);
            else if (dname[0]) snprintf(named, sizeof named, "%s · %s", dname, capacity);
            else snprintf(named, sizeof named, "%s", capacity);
            Text ct = text_make(cr, FONT_SANS, g->slot_h >= N_CLONE_TITLE ? N_HEADER_FONT : N_IMAGE_SUB,
                                selected ? 700 : 600, named, 0,
                                !mapped || dname[0] ? DIR_RTL : DIR_LTR);
            cairo_save(cr);
            cairo_rectangle(cr, slot.x + 2, slot.y + 1, slot.w - 4, slot.h - 2);
            cairo_clip(cr);
            text_draw(cr, &ct, slot.x + (slot.w - ct.w) / 2,
                      slot.y + (slot.h - ct.h) / 2, selected ? t->on_ink : !avail ? t->muted : t->ink);
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
        if (avail)
            hit_add(a, slot, HIT_ROOM_DISK_BASE + mi * MAX_DRAWERS + j);
        /* #1269: a chosen disk's drivers tag in pick mode -- after the slot, so its hit wins */
        if (avail && selected) room_drv_tag_draw(a, cr, slot, m, mi, j);
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
            char label[SLOT_NAME_MAX + 64]; room_drawer_label(m, d, label, sizeof label);
            int k = snprintf(line, cap - used, "%sלא ניתן לזהות את הכונן — %s · %s",
                              used ? "\n" : "", label, m->name[0] ? m->name : m->mac);
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
        for (int j = 0; j < m->nroom_drawers; j++) if (m->room_drawers[j].present) {
            const RoomDrawer *v = &m->room_drawers[j]; int k = nfound;
            while (k > 0 && room_drawer_after(found[k - 1], v)) { found[k] = found[k - 1]; k--; }
            found[k] = v; nfound++;
        }
        int slots = nfound ? nfound : 1;
        double slot_h = body.h / slots;
        for (int k = 0; k < slots; k++) {
            const RoomDrawer *d = nfound ? found[k] : NULL;
            /* #704: the slot's name instead of "דיסק N" when the operator gave one */
            char size[32] = "—", label[SLOT_NAME_MAX + 128], base[SLOT_NAME_MAX + 64], percent[16];
            if (d && d->size_bytes) fmt_bytes(size, sizeof size, (double)d->size_bytes);
            /* #1495: "דיסק N" by the drawer (room_drawer_label), never by port */
            if (d) { room_drawer_label(m, d, base, sizeof base); snprintf(label, sizeof label, "%s  %s", base, size); }
            else   snprintf(label, sizeof label, "%s", "לא דווחו דיסקים");
            snprintf(percent, sizeof percent, "%d%%", pct);
            Text lt = rtl_block_make(cr, 11, 500, label, body.w - 48);
            Text pt = text_make(cr, FONT_MONO, 11, 500, percent, 0, DIR_LTR);
            double sy = body.y + k * slot_h;
            text_draw_r(cr, &lt, body.x + body.w, sy, t->ink);
            text_draw(cr, &pt, body.x, sy, t->muted);
            Rgb bar = failed ? t->danger : partial ? t->warn :
                      d ? room_drawer_color(t, d) : t->bar_done;
            draw_thin_bar(cr, t, (Rect){body.x, sy + 15, body.w, N_BAR_SM}, pct, bar);
            text_free(&lt); text_free(&pt);
        }
        if (!on) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, N_DIM_ALPHA); }
    }
}

/* mockup 10/10 ("חדר + סבב חי"): "עצור סבב…" stands apart at the start as
 * red words; it opens the #1270 confirm dialog (the red "עצור סבב" is
 * there). The rest sit at the inline end, the primary last. */
/* #989 item 3: a wave that finished with work left -- the next one is open and
 * waiting for replaced disks. Same test as the console (roomWaveProgress). */
static int wave_ended(const State *s) { return s->wave_open && s->wave_number > 1 && s->remaining > 0; }

static void live_footer(App *a, cairo_t *cr, Rect foot) {
    const Theme *t = a->theme; const State *s = &a->st;
    double bh = btn_height(cr), by = foot.y + N_ACTION_TOP;
    Text stop = btn_label(cr, "עצור סבב…");
    double sw = btn_width(&stop), sx = foot.x + foot.w - N_PAD - sw;
    draw_btn(a, cr, (Rect){ sx, by, sw, bh }, &stop, BTN_GHOST_DANGER, HIT_ROOM_CLOSE);
    text_free(&stop);
    if(a->admin){
        Text all=btn_label(cr,"כבה את כל החדר");double aw=btn_width(&all);sx-=aw+8;
        draw_btn(a,cr,(Rect){sx,by,aw,bh},&all,BTN_GHOST_DANGER,HIT_ROOM_POWEROFF_ALL);text_free(&all);
    }
    Text labels[3]; int kinds[3], ids[3], nb = 0;     /* from the inline end: the primary first */
    if (s->wave_open) { labels[nb] = btn_label(cr, "התחל עכשיו"); kinds[nb] = BTN_PRIMARY; ids[nb++] = HIT_ROOM_START; }
    labels[nb] = btn_label(cr, wave_ended(s) ? "הער את מחשבי השיכפול" : "העֵר שוב"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_ROOM_WAKE;
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

/* #432 + #65: what an open wave says before it starts, in one orange box
 * (the standby note's language: warning_soft, a warn line, orange dots).
 *   - round.precheck: a drawer on a slow link or a risen CRC -- a warning,
 *     never a block; "התחל" then asks (CONFIRM_ROOM_START_WARN).
 *   - round.missing: who the wave is waiting for, with "כבה אותו" for one
 *     that still answers, and the other two choices of the spec: "התחל בכל
 *     זאת" (room-start) and "בטל את הסבב" (the #1270 stop dialog).
 * The lines are the server's text_he, the same words as the console.
 * draw = 0 measures only (no hits), so the card can be sized first. */
static double room_pre_block(App *a, cairo_t *cr, double x, double y, double w, int draw) {
    const Theme *t = a->theme; const State *s = &a->st;
    if (!s->wave_open || (!s->nroom_warn && !s->nroom_missing)) return 0;
    double pad = N_ALERT_PAD_X, inner = w - 2 * pad - 14, cy = y + N_ALERT_PAD_Y, bh = btn_height(cr);
    double right = x + w - pad;
    if (draw) {
        double h = room_pre_block(a, cr, x, y, w, 0);
        Rect box = { x, y, w, h };
        draw_fill_rrect(cr, box, RADIUS_SM, t->warning_soft);
        draw_border_rrect(cr, box, RADIUS_SM, t->warn, 1);
    }
    char line[400];
    if (s->nroom_warn) {
        snprintf(line, sizeof line, "לפני ההתחלה — %d %s בגל עם קישור חשוד",
                 s->nroom_warn + s->hidden_room_warn, s->nroom_warn + s->hidden_room_warn == 1 ? "מגירה" : "מגירות");
        Text h = rtl_block_make(cr, N_SUB, 600, line, inner + 14);
        if (draw) text_draw_r(cr, &h, right, cy, t->warning_ink);
        cy += h.h + 6; text_free(&h);
        for (int i = 0; i < s->nroom_warn; i++) {
            char rl[sizeof s->room_warn[0] + 4];     /* RLM: "CLONE-01 · ..." stays an RTL line */
            snprintf(rl, sizeof rl, RLM "%s", s->room_warn[i]);
            Text l = rtl_block_make(cr, N_SUB, 400, rl, inner);
            if (draw) {
                cairo_arc(cr, right - 4, cy + l.h / 2, 4, 0, 2 * M_PI); draw_set(cr, t->warn); cairo_fill(cr);
                text_draw_r(cr, &l, right - 14, cy, t->warning_ink);
            }
            cy += l.h + 4; text_free(&l);
        }
        if (s->hidden_room_warn) {
            snprintf(line, sizeof line, "ועוד %d — ראו בקונסולה", s->hidden_room_warn);
            Text l = rtl_block_make(cr, N_SUB, 400, line, inner);
            if (draw) text_draw_r(cr, &l, right - 14, cy, t->warning_ink);
            cy += l.h + 4; text_free(&l);
        }
        Text f = rtl_block_make(cr, N_SUB, 400, "הגל ייכתב גם כך — אזהרה, לא חסימה. התחלה עם האזהרה נרשמת ביומן.", inner + 14);
        if (draw) text_draw_r(cr, &f, right, cy, t->muted);
        cy += f.h + (s->nroom_missing ? N_ACTION_GAP : 0); text_free(&f);
    }
    if (s->nroom_missing) {
        int n = s->nroom_missing + s->hidden_room_missing;
        if (n == 1) snprintf(line, sizeof line, "חסרים בגל: מחשב אחד — %d מגירות מוכנות, והגל לא יוצא מקוצר מעצמו.", s->ready);
        else snprintf(line, sizeof line, "חסרים בגל: %d מחשבים — %d מגירות מוכנות, והגל לא יוצא מקוצר מעצמו.", n, s->ready);
        Text h = rtl_block_make(cr, N_SUB, 600, line, inner + 14);
        if (draw) text_draw_r(cr, &h, right, cy, t->warning_ink);
        cy += h.h + 6; text_free(&h);
        for (int i = 0; i < s->nroom_missing; i++) {
            Text off = btn_label(cr, "כבה אותו");
            double ow = s->room_missing[i].can_poweroff ? btn_width(&off) : 0;
            char rl[sizeof s->room_missing[0].text + 4];
            snprintf(rl, sizeof rl, RLM "%s", s->room_missing[i].text);
            Text l = rtl_block_make(cr, N_SUB, 400, rl, inner - (ow ? ow + N_ACTION_GAP : 0));
            double row = ow ? fmax(l.h, bh) : l.h;
            if (draw) {
                cairo_arc(cr, right - 4, cy + row / 2, 4, 0, 2 * M_PI); draw_set(cr, t->warn); cairo_fill(cr);
                text_draw_r(cr, &l, right - 14, cy + (row - l.h) / 2, t->warning_ink);
                if (ow) draw_btn(a, cr, (Rect){ x + pad, cy, ow, bh }, &off, BTN_GHOST_DANGER, HIT_ROOM_POWEROFF_BASE + i);
            }
            cy += row + 4; text_free(&l); text_free(&off);
        }
        if (s->hidden_room_missing) {
            snprintf(line, sizeof line, "ועוד %d — ראו בקונסולה", s->hidden_room_missing);
            Text l = rtl_block_make(cr, N_SUB, 400, line, inner);
            if (draw) text_draw_r(cr, &l, right - 14, cy, t->warning_ink);
            cy += l.h + 4; text_free(&l);
        }
        /* the spec's other two choices, at the inline end: start anyway, cancel */
        cy += 4;
        if (draw) {
            Text go = btn_label(cr, "התחל בכל זאת"), no = btn_label(cr, "בטל את הסבב…");
            double gw = btn_width(&go), nw = btn_width(&no);
            draw_btn(a, cr, (Rect){ x + pad, cy, gw, bh }, &go, BTN_WARN, HIT_ROOM_START);
            draw_btn(a, cr, (Rect){ x + pad + gw + 8, cy, nw, bh }, &no, BTN_GHOST_DANGER, HIT_ROOM_CLOSE);
            text_free(&go); text_free(&no);
        }
        cy += bh;
    }
    return cy + N_ALERT_PAD_Y - y;
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
            if (room_rows(&s->machines[i]) > max_slots) max_slots = room_rows(&s->machines[i]);
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
        /* Nadav 16/10: no round yet -- waking acts on nothing, so the button is grey and takes no click */
        static const int kinds[3] = { BTN_PRIMARY, BTN_OFF, BTN_PLAIN };
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
    char sub[300], count[48], hint[1200], hint0[520], prog[300], est[96], pace[160], age[64], image[112];
    /* #410: how old this picture is, next to the wave -- a state file that
     * stopped changing shows a growing number, not a current-looking screen. */
    pace_age(s->updated, age, sizeof age);
    bidi_isolate(s->round_image, image, sizeof image);   /* a Latin-first name stays one run */
    snprintf(sub, sizeof sub, RLM "משדר: %s · גל %d%s%s", image, s->wave_number,
             age[0] ? " · " : "", age);
    snprintf(count, sizeof count, "%d / %d", s->written, s->target);
    if (s->wave_open) {
        int need = s->remaining - s->ready; if (need < 0) need = 0;
        snprintf(hint0, sizeof hint0, "הגל ממתין: %d מגירות מוכנות, צריך עוד %d — או \"התחל עכשיו\". החלפתם מגירות? הדליקו את המכונות והן יצטרפו.",
                 s->ready, need);
    } else {
        /* #410: elapsed, rate and ETA come from the server (round= record);
         * "לא נמדד" in words when it has nothing yet -- never a 0 rate. */
        pace_text(s->elapsed_s, s->rate_bps, s->eta_s, 0, pace, sizeof pace);
        snprintf(hint0, sizeof hint0, "הגל משדר — %s. מכונה שסיימה — מכבים, מחליפים מגירות, מדליקים.",
                 pace[0] ? pace : "זמן וקצב טרם נמדדו");
    }
    /* #989 item 3: where the round stands across waves, from the round= record only;
     * K = ceil(left / ready now), and no estimate at all when nothing is ready. */
    prog[0] = 0;
    if (s->remaining > 0) {
        if (s->ready > 0) snprintf(est, sizeof est, "משוער: עוד %d גלים", (s->remaining + s->ready - 1) / s->ready);
        else snprintf(est, sizeof est, "ממתין לדיסקים חדשים");
        snprintf(prog, sizeof prog, "גל %d · נכתבו %d מתוך %d · נשארו %d · %s\n",
                 s->wave_number, s->written, s->target, s->remaining, est);
    }
    snprintf(hint, sizeof hint, "%s%s%s", prog, hint0,
             wave_ended(s) ? "\nהגל הסתיים — החליפו דיסקים והעירו את מחשבי השיכפול כדי להמשיך" : "");
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
    double pre_h = room_pre_block(a, cr, 0, 0, inner, 0);       /* #432/#65 */
    double pre_gap = pre_h > 0 ? N_ACTION_GAP : 0;
    double body_h = N_PAD + pl.h + N_LABEL_GAP + N_BAR_H + N_ACTION_GAP + alert_height(&hint_t) + pre_gap + pre_h + N_CHOICE_GAP + rows_h + unid_h + drv_res_h + N_LABEL_GAP + err_h + N_PAD;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + N_PAD, y = body.y + N_PAD;
    text_draw_r(cr, &pl.big, x + inner, y, t->ink);
    text_draw_r(cr, &pl.small, x + inner - pl.big.w - 10, y + pl.drop, t->ink);
    text_free(&pl.big); text_free(&pl.small);             y += pl.h + N_LABEL_GAP;
    draw_big_bar(cr, t, (Rect){ x, y, inner, N_BAR_H }, pct, 0); y += N_BAR_H + N_ACTION_GAP;
    draw_alert(cr, t, (Rect){ x, y, inner, alert_height(&hint_t) }, &hint_t); y += alert_height(&hint_t);
    if (pre_h > 0) { y += pre_gap; room_pre_block(a, cr, x, y, inner, 1); y += pre_h; }   /* #432/#65 */
    y += N_CHOICE_GAP;
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
