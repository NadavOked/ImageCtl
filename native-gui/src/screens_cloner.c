#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"

/* #1090 / mockup 10/10 ("מסך מחשב השיכפול"): one card per disk this cloner
 * FOUND -- a drawer of the round or a disk of the idle inventory -- not a
 * fixed three: cloner 1 has two SATA ports. Order is the physical SATA port,
 * never kernel device order. A card is as tall as what it has to say. */

static const char *drawer_state_he(const char *s) {
    if (!strcmp(s, "writing")) return "כותב";
    if (!strcmp(s, "verifying")) return "מאמת";
    if (!strcmp(s, "done")) return "הסתיים";
    if (!strcmp(s, "failed")) return "נכשל";
    if (!strcmp(s, "skipped")) return "מדלג";
    if (!strcmp(s, "replacing")) return "ממתין להחלפה";
    return "ממתין";
}

typedef struct {
    int port, synthetic;
    const Drawer *drawer;
    const IdleDisk *idle;
} ClonerEntry;

static int entry_drawer(const ClonerEntry *e) {
    return e->drawer ? eff_drawer(e->drawer->drawer, e->port) :
           e->idle ? eff_drawer(e->idle->drawer, e->port) : 0;
}

static int entry_after(const ClonerEntry *a, const ClonerEntry *b) {
    int ad = entry_drawer(a), bd = entry_drawer(b);
    if (ad && bd) return ad > bd;
    if (ad != 0) return 0;
    if (bd != 0) return 1;
    return a->port > b->port;
}

static int cloner_entries(const State *s, ClonerEntry *out) {
    int n = 0;
    if (s->ndrawers > 0) {
        for (int i = 0; i < s->ndrawers; i++)
            out[n++] = (ClonerEntry){ s->drawers[i].port, 0, &s->drawers[i],
                                      idle_for_port(s, s->drawers[i].port) };
    } else {
        for (int i = 0; i < s->nidisks; i++)
            out[n++] = (ClonerEntry){ s->idisks[i].port, 0, NULL, &s->idisks[i] };
    }
    if (s->smart_pending) {
        int found = 0;
        for (int i = 0; i < n; i++) if (out[i].port == s->smart_port) found = 1;
        if (!found && n < MAX_DRAWERS + 1)
            out[n++] = (ClonerEntry){ s->smart_port, 1, NULL, NULL };
    }
    for (int i = 1; i < n; i++) {
        ClonerEntry v = out[i]; int j = i;
        while (j > 0 && entry_after(&out[j - 1], &v)) { out[j] = out[j - 1]; j--; }
        out[j] = v;
    }
    return n;
}

static void entry_label(const State *s, const ClonerEntry *e, char *out, size_t n) {
    int bay = entry_drawer(e);
    if (bay > 0) cloner_slot_label(s, bay, out, n);
    else if (e->synthetic) cloner_slot_label(s, e->port, out, n);
    else {
        const char *name = e->port >= 1 && e->port <= MAX_DRAWERS ? s->cslot_names[e->port - 1] : "";
        const char *conn = e->drawer ? e->drawer->conn : e->idle ? e->idle->conn : "";
        const char *dev = e->drawer ? e->drawer->dev : e->idle ? e->idle->dev : "";
        unmapped_label(name, conn, e->port, dev, out, n);
    }
}

static void cloner_title(cairo_t *cr, const Theme *t, const State *s, int ndisks,
                         double W, double *y) {
    Text h = rtl_block_make(cr, N_TITLE, 600, "שיכפול במקביל", W - 2 * N_PAD);
    /* #410: the snapshot's age next to the image name -- a kiosk that
     * stopped writing the state shows a growing number, not a live look. */
    char sub[400], age[64], count[48], image[112]; pace_age(s->updated, age, sizeof age);
    if (ndisks == 1) snprintf(count, sizeof count, "דיסק אחד");
    else if (ndisks) snprintf(count, sizeof count, "%d דיסקים", ndisks);
    else             snprintf(count, sizeof count, "לא נמצאו דיסקים");
    /* the name is an isolated run and the line opens with an RLM: a Latin-first
     * name ("Office 2024 — סטנדרט") made auto-dir turn the whole line LTR */
    bidi_isolate(s->cloner_image[0] ? s->cloner_image : "ממתין לשידור מהשרת", image, sizeof image);
    snprintf(sub, sizeof sub, RLM "%s · %s%s%s", image, count, age[0] ? " · " : "", age);
    Text p = rtl_block_make(cr, 13, 400, sub, W - 2 * N_PAD);
    text_draw_r(cr, &h, W - N_PAD, *y, t->ink); *y += h.h + 4;
    text_draw_r(cr, &p, W - N_PAD, *y, t->muted); *y += p.h + 12;
    cairo_rectangle(cr, N_PAD, *y, W - 2 * N_PAD, 1);
    draw_set(cr, t->hair); cairo_fill(cr); *y += 14;
    text_free(&h); text_free(&p);
}

static Rgb drawer_accent(const Theme *t, const Drawer *d, int pending) {
    if (pending) return t->warn; /* writing is paused, awaiting an operator */
    if (d && (!strcmp(d->state, "failed") || !strcmp(d->state, "skipped")))
        return t->danger;
    if (d && !strcmp(d->state, "done")) return t->led_ok;
    if (d && (!strcmp(d->state, "writing") || !strcmp(d->state, "verifying")))
        return t->indigo;
    return t->led_idle;
}

static int smart_pill_kind(const char *smart) {
    int lvl = smart_level(smart);
    if (!smart[0] || !strcmp(smart, "unchecked")) return PILL_OFF;
    return lvl == 2 ? PILL_BAD : lvl == 1 ? PILL_WARN : PILL_OK;
}

/* One disk card. With draw 0 it only measures: the cards of a row share the
 * row's height, so every card is measured before any is drawn. Returns the
 * card's content height. */
static double disk_card(App *a, cairo_t *cr, Rect card, const ClonerEntry *entry, int draw) {
    const Theme *t = a->theme;
    int port = entry->port;
    const Drawer *d = entry->drawer;
    const IdleDisk *idle = entry->idle;
    int pending = a->st.smart_pending && a->st.smart_port == port;
    Rgb accent = drawer_accent(t, d, pending);
    if (!d && idle) accent = smart_color(t, idle->smart);
    double w = card.w - 40, right = card.x + card.w - 20, y = card.y + 18;
    if (draw) {
        draw_fill_rrect(cr, card, t->radius, t->surface);
        if (pending) draw_border_rrect(cr, card, t->radius, t->warn, 2);
        else {
            draw_border_rrect(cr, card, t->radius, t->hair, 1);
            /* the state's colour as a 3px top edge (the 5px side bar sat
             * under the rounded corner) */
            cairo_save(cr); draw_rrect_path(cr, card, t->radius); cairo_clip(cr);
            cairo_rectangle(cr, card.x, card.y, card.w, 3); draw_set(cr, accent); cairo_fill(cr);
            cairo_restore(cr);
        }
    }

    /* the head: "דיסק N" -- or the operator's name of the slot (#704) -- at
     * the start, the state pill at the end */
    char disk_name[SLOT_NAME_MAX + 16];
    entry_label(&a->st, entry, disk_name, sizeof disk_name);
    Text name = rtl_block_make(cr, 16, 600, disk_name, w);
    if (draw) {
        const char *word = pending ? "נדרשת החלטה" : drawer_state_he(d ? d->state : "waiting");
        int kind = pending ? PILL_WARN : d && (!strcmp(d->state, "failed") ||
                   !strcmp(d->state, "skipped")) ? PILL_BAD :
                   d && !strcmp(d->state, "done") ? PILL_OK : d ? PILL_INFO : PILL_OFF;
        text_draw_r(cr, &name, right, y + (24 - name.h) / 2, t->ink);
        draw_pill(cr, t, card.x + 20 + pill_width(cr, word, 1), y, word, kind, 1);
    }
    text_free(&name);
    y += 24;

    /* the SMART chip says what the disk reported, in words */
    const char *smart = pending ? a->st.smart_verdict : idle ? idle->smart : "";
    char smart_label[64];
    snprintf(smart_label, sizeof smart_label, "SMART %s",
             pending ? smart_he(a->st.smart_verdict) : idle ? smart_he(idle->smart) : "לא דווח");
    const char *model = idle && idle->model[0] ? idle->model : d && d->dev[0] ? d->dev : "";

    int active = d && (d->total || d->bytes || strcmp(d->state, "waiting"));
    if (pending) {
        /* the decision is the card: what failed, and that nothing was written */
        Text why = rtl_block_make(cr, 15, 600, a->st.smart_reason[0] ? smart_reason_text(a->st.smart_reason)
                                                                      : smart_he(a->st.smart_verdict), w);
        Text held = rtl_block_make(cr, 12, 400, "הכתיבה מושהית עד שתחליטו. הדיסק לא נכתב.", w);
        y += 14;
        if (draw) text_draw_r(cr, &why, right, y, t->ink);
        y += why.h + 6;
        if (draw) text_draw_r(cr, &held, right, y, t->muted);
        y += held.h;
        text_free(&why); text_free(&held);
    } else if (active) {
        int pct = -1;
        if (d->total) {
            pct = (int)floor(100.0 * d->bytes / d->total + .5);
            if (pct > 100) pct = 100;
        }
        char pct_text[24] = "";
        if (pct >= 0) snprintf(pct_text, sizeof pct_text, "%d%%", pct);
        y += 10;
        if (pct_text[0]) {
            Text big = text_make(cr, FONT_MONO, 44, 400, pct_text, 0, DIR_LTR);
            if (draw) text_draw_r(cr, &big, right, y, t->ink);
            y += big.h + 6;
            text_free(&big);
        }
        if (draw) draw_thin_bar(cr, t, (Rect){card.x + 20, y, w, N_BAR_SM}, pct < 0 ? 0 : pct, accent);
        y += N_BAR_SM + 12;
        char written[40] = "—", total[40] = "—", line[96];
        fmt_bytes(written, sizeof written, (double)d->bytes);
        if (d->total) fmt_bytes(total, sizeof total, (double)d->total);
        snprintf(line, sizeof line, "%s / %s", written, total);
        /* #410: rate and ETA the kiosk measured on this drawer (drawer=
         * fields 7-8); -1 / absent = not measured -- never drawn as 0. */
        char pace[96] = "";
        if (d->rate_bps > 0) {
            char r[40]; fmt_bytes(r, sizeof r, (double)d->rate_bps);
            if (d->eta_s >= 0) snprintf(pace, sizeof pace, "%s/s · %d:%02d:%02d", r, d->eta_s / 3600, d->eta_s / 60 % 60, d->eta_s % 60);
            else snprintf(pace, sizeof pace, "%s/s", r);
        }
        Text wl = text_make(cr, FONT_MONO, 12, 400, line, 0, DIR_LTR);
        Text pl = text_make(cr, pace[0] ? FONT_MONO : FONT_SANS, 12, 500, pace[0] ? pace : "קצב טרם נמדד", 0, pace[0] ? DIR_LTR : DIR_RTL);
        if (draw) { text_draw_r(cr, &wl, right, y, t->muted); text_draw(cr, &pl, card.x + 20, y, pace[0] ? t->ink : t->muted); }
        y += dmax(wl.h, pl.h);
        text_free(&wl); text_free(&pl);
    } else {
        /* waiting: one line, not nine rows of "—" */
        char what[160];
        if (idle && idle->model[0]) {
            char size[32]; fmt_bytes(size, sizeof size, (double)idle->size);
            snprintf(what, sizeof what, "%s · %s · ממתין לשידור", idle->model, size);
        } else snprintf(what, sizeof what, "%s", d ? "ממתין לשידור" : "לא זוהה דיסק בפורט הזה");
        Text wt = rtl_block_make(cr, 13, 400, what, w);
        y += 14;
        if (draw) text_draw_r(cr, &wt, right, y, t->muted);
        y += wt.h;
        text_free(&wt);
        model = "";                                     /* already said in the line */
    }

    /* chips: SMART, then the model -- right to left */
    if (pending || active || idle) {
        y += 12;
        if (draw) { cairo_rectangle(cr, card.x + 20, y, w, 1); draw_set(cr, t->hair); cairo_fill(cr); }
        y += 12;
        if (draw) {
            double cx = right;
            cx -= draw_pill(cr, t, cx, y, smart_label, smart[0] ? smart_pill_kind(smart) : PILL_OFF, 0) + 6;
            if (model[0]) draw_pill(cr, t, cx, y, model, PILL_OFF, 0);
        }
        y += 24;
    }

    if (d && d->error[0]) {
        /* #943: a done disk that still carries an error is a warning
         * (orange), not a failed disk (red) -- the restore completed. */
        Text error = error_make(cr, d->error, w);
        y += 10;
        if (draw) text_draw_r(cr, &error, right, y, !strcmp(d->state, "done") ? t->warn : t->danger);
        y += error.h;
        text_free(&error);
    }
    return y + 18 - card.y;
}

/* A footer button with its key in a chip inside the label (mockup 10/10:
 * "החלף דיסק R"). Returns the width; draws only when draw is set. */
static double key_button(App *a, cairo_t *cr, double x, double y, const char *label,
                         const char *key, int kind, int id, int draw) {
    char chip_bg[8], chip_fg[8], e[160], mk[400];
    Rgb bg = a->theme->porcelain, fg = a->theme->ink;
    snprintf(chip_bg, sizeof chip_bg, "#%02X%02X%02X", (int)(bg.r * 255 + .5), (int)(bg.g * 255 + .5), (int)(bg.b * 255 + .5));
    snprintf(chip_fg, sizeof chip_fg, "#%02X%02X%02X", (int)(fg.r * 255 + .5), (int)(fg.g * 255 + .5), (int)(fg.b * 255 + .5));
    text_escape(label, e, sizeof e);
    snprintf(mk, sizeof mk, "%s  <span font_family=\"" FONT_MONO_FAMILY "\" size=\"smaller\" background=\"%s\" foreground=\"%s\"> %s </span>",
             e, chip_bg, chip_fg, key);
    Text l = text_make_markup(cr, FONT_SANS, N_SUB, 500, mk, 0, DIR_RTL);
    double bw = btn_width(&l);
    if (draw) draw_btn(a, cr, (Rect){ x, y, bw, N_BUTTON_H }, &l, kind, id);
    text_free(&l);
    return bw;
}

void screen_cloner(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    ClonerEntry entries[MAX_DRAWERS + 1]; int n = cloner_entries(&a->st, entries);
    double y = head_h + 18;
    cloner_title(cr, t, &a->st, n, W, &y);

    double gap = 14, bar_h = a->st.smart_pending ? 74 : 0;
    int cols = n <= 3 ? (n ? n : 1) : (n + 1) / 2;
    double card_w = (W - 2 * N_PAD - (cols - 1) * gap) / cols;
    if (!n) {
        Text none = rtl_block_make(cr, 15, 400, "המחשב הזה לא דיווח על דיסקים מחוברים. חברו דיסק והפעילו מחדש.", W - 2 * N_PAD);
        text_draw_r(cr, &none, W - N_PAD, y, t->muted);
        text_free(&none);
    }
    for (int k = 0; k < n; k += cols) {
        double row_h = 0;
        for (int j = k; j < k + cols && j < n; j++)
            row_h = dmax(row_h, disk_card(a, cr, (Rect){ 0, 0, card_w, 0 }, &entries[j], 0));
        for (int j = k; j < k + cols && j < n; j++) {
            Rect card = { W - N_PAD - card_w - (j - k) * (card_w + gap), y, card_w, row_h };
            disk_card(a, cr, card, &entries[j], 1);
        }
        y += row_h + gap;
    }

    if (a->st.smart_pending) {
        /* the question owns the bottom of the screen: a full-width bar, the
         * disk and its trouble at the start, the answers ordered by risk --
         * replace (safe), skip, and "write anyway" as a warning outline, not
         * the primary blue (mockup 10/10; it was the other way round). */
        Rect bar = { 0, H - N_STATUS_H - bar_h, W, bar_h };
        draw_fill_rrect(cr, bar, 0, t->surface);
        cairo_rectangle(cr, 0, bar.y, W, 2); draw_set(cr, t->warn); cairo_fill(cr);
        int red = !strcmp(a->st.smart_verdict, "failed_last");
        char head[SLOT_NAME_MAX + 16 + 160], disk[SLOT_NAME_MAX + 16];
        const char *why = a->st.smart_reason[0] ? smart_reason_text(a->st.smart_reason) : smart_he(a->st.smart_verdict);
        cloner_port_label(&a->st, a->st.smart_port, disk, sizeof disk);   /* #1495 */
        snprintf(head, sizeof head, "%s · %s.", disk, why);
        Text h1 = rtl_block_make(cr, 13, 600, head, W / 2);
        Text h2 = rtl_block_make(cr, 12, 400, "מה לעשות עם הדיסק הזה?", W / 2);
        double ty = bar.y + (bar.h - h1.h - 2 - h2.h) / 2;
        text_draw_r(cr, &h1, W - N_PAD, ty, t->ink);
        text_draw_r(cr, &h2, W - N_PAD, ty + h1.h + 2, t->muted);
        text_free(&h1); text_free(&h2);
        double by = bar.y + (bar.h - N_BUTTON_H) / 2, bw[3], total = 0;
        /* measured first (id 0: nothing is registered), drawn from the inline end */
        bw[0] = key_button(a, cr, 0, by, "החלף דיסק", "R", BTN_PLAIN, 0, 0);
        bw[1] = key_button(a, cr, 0, by, red ? "המשך בלי הדיסק" : "דלג על הדיסק", red ? "Enter" : "S", BTN_PLAIN, 0, 0);
        bw[2] = red ? 0 : key_button(a, cr, 0, by, "כתוב בכל זאת…", "Enter", BTN_WARN, 0, 0);   /* 16/10: it opens the SMART window */
        for (int i = 0; i < 3; i++) total += bw[i] + (bw[i] > 0 && i ? 10 : 0);
        double x = N_PAD + total;                      /* the group ends at the inline end */
        x -= bw[0]; key_button(a, cr, x, by, "החלף דיסק", "R", BTN_PLAIN, HIT_SMART_REPLACE, 1);
        x -= 10 + bw[1]; key_button(a, cr, x, by, red ? "המשך בלי הדיסק" : "דלג על הדיסק", red ? "Enter" : "S", BTN_PLAIN, HIT_SMART_SKIP, 1);
        if (!red) {
            x -= 10 + bw[2]; key_button(a, cr, x, by, "כתוב בכל זאת…", "Enter", BTN_WARN, HIT_SMART_RESCUE, 1);
        }
    }
}
