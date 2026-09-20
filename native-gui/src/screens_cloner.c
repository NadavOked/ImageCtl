#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"

/* #1090: a cloner is a three-bay appliance.  Each bay owns a full-height
 * card; drawer order is the physical SATA port, never kernel device order. */

static const char *drawer_state_he(const char *s) {
    if (!strcmp(s, "writing")) return "כותב";
    if (!strcmp(s, "verifying")) return "מאמת";
    if (!strcmp(s, "done")) return "הסתיים";
    if (!strcmp(s, "failed")) return "נכשל";
    if (!strcmp(s, "skipped")) return "מדלג";
    if (!strcmp(s, "replacing")) return "ממתין להחלפה";
    return "ממתין";
}

static const Drawer *drawer_for_port(const State *s, int port) {
    for (int i = 0; i < s->ndrawers; i++)
        if (s->drawers[i].port == port) return &s->drawers[i];
    return NULL;
}

static void cloner_title(cairo_t *cr, const Theme *t, const State *s,
                         double W, double *y) {
    Text h = rtl_block_make(cr, N_TITLE, 600, "שיכפול במקביל", W - 2 * N_PAD);
    /* #410: the snapshot's age next to the image name -- a kiosk that
     * stopped writing the state shows a growing number, not a live look. */
    char sub[260], age[64]; pace_age(s->updated, age, sizeof age);
    snprintf(sub, sizeof sub, "שלושה דיסקים · %s%s%s",
             s->cloner_image[0] ? s->cloner_image : "ממתין לשידור מהשרת",
             age[0] ? " · " : "", age);
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

/* The prompt's reason is the agent's code word (smart.sh: smart_classify /
 * disk_failure_cause); the screen says it in words, and shows an unknown
 * code as-is rather than hiding it. */
static const char *smart_reason_he(const char *reason) {
    if (!reason || !reason[0] || !strcmp(reason, "ok")) return "";
    if (!strcmp(reason, "health_failed")) return "בדיקת הבריאות של הדיסק נכשלה";
    if (!strcmp(reason, "no_health")) return "הדיסק אינו מדווח על בריאות";
    if (!strcmp(reason, "no_smartctl")) return "smartctl חסר ב-initramfs";
    if (!strcmp(reason, "pending")) return "ממתין להכרעה";
    return reason;
}

static void smart_cell(cairo_t *cr, const Theme *t, Rect r,
                       const char *label, const char *value, Rgb value_color) {
    draw_fill_rrect(cr, r, t->radius, t->field);
    Text l = rtl_block_make(cr, 10, 400, label, r.w - 16);
    Text v = text_make(cr, FONT_MONO, 13, 600, value, 0, DIR_LTR);
    text_draw_r(cr, &l, r.x + r.w - 8, r.y + 7, t->muted);
    text_draw_r(cr, &v, r.x + r.w - 8, r.y + 24, value_color);
    text_free(&l); text_free(&v);
}

static void action_button(App *a, cairo_t *cr, Rect r, const char *label,
                          int kind, int hit) {
    Text b = btn_label(cr, label);
    draw_btn(a, cr, r, &b, kind, hit);
    text_free(&b);
}

static void disk_card(App *a, cairo_t *cr, Rect card, int port,
                      const Drawer *d, const IdleDisk *idle, double pct_size) {
    const Theme *t = a->theme;
    int pending = a->st.smart_pending && a->st.smart_port == port;
    Rgb accent = drawer_accent(t, d, pending);
    if (!d && idle) accent = smart_color(t, idle->smart);
    draw_fill_rrect(cr, card, t->radius, t->surface);
    draw_border_rrect(cr, card, t->radius, pending ? accent : t->hair,
                      pending ? 2 : 1);
    cairo_rectangle(cr, card.x, card.y, 5, card.h);
    draw_set(cr, accent); cairo_fill(cr);

    double x = card.x + 24, right = card.x + card.w - 24, y = card.y + 22;
    char disk_name[40]; snprintf(disk_name, sizeof disk_name, "דיסק %d", port);
    Text name = rtl_block_make(cr, 16, 600, disk_name, card.w - 48);
    text_draw_r(cr, &name, right, y, t->ink);
    double pill_w = draw_pill(cr, t, right, y + name.h + 7,
                              pending ? "נדרשת החלטה" : drawer_state_he(d ? d->state : "waiting"),
                              pending ? PILL_WARN : d && (!strcmp(d->state, "failed") ||
                              !strcmp(d->state, "skipped")) ? PILL_BAD :
                              d && !strcmp(d->state, "done") ? PILL_OK : PILL_INFO, 1);
    (void)pill_w;
    y += name.h + 39;

    int pct = -1;
    if (d && d->total) {
        pct = (int)floor(100.0 * d->bytes / d->total + .5);
        if (pct > 100) pct = 100;
    }
    char pct_text[24];
    if (pct >= 0) snprintf(pct_text, sizeof pct_text, "%d%%", pct);
    else snprintf(pct_text, sizeof pct_text, "—");
    Text big = text_make(cr, FONT_MONO, pct_size, 400, pct_text, 0, DIR_LTR);
    text_draw(cr, &big, card.x + (card.w - big.w) / 2, y, accent);
    y += big.h + 9;
    draw_thin_bar(cr, t, (Rect){x, y, card.w - 48, N_BAR_SM}, pct < 0 ? 0 : pct, accent);
    y += N_BAR_SM + 18;

    char written[40] = "—", total[40] = "—";
    if (d) fmt_bytes(written, sizeof written, (double)d->bytes);
    if (d && d->total) fmt_bytes(total, sizeof total, (double)d->total);
    char written_line[96]; snprintf(written_line, sizeof written_line, "%s / %s", written, total);
    const char *model = idle && idle->model[0] ? idle->model : d && d->dev[0] ? d->dev : "לא זוהה דיסק";
    /* #410: rate and ETA the kiosk measured on this drawer (drawer= fields
     * 7-8); -1 / absent = not measured, drawn as a dash -- never as 0. */
    char rate[40] = "—", eta[40] = "—";
    if (d && d->rate_bps > 0) { fmt_bytes(rate, sizeof rate, (double)d->rate_bps); strncat(rate, "/s", sizeof rate - strlen(rate) - 1); }
    if (d && d->eta_s >= 0) snprintf(eta, sizeof eta, "%d:%02d:%02d", d->eta_s / 3600, d->eta_s / 60 % 60, d->eta_s % 60);
    const char *metric_label[4] = {"נכתב", "קצב", "זמן נותר", "דגם"};
    const char *metric_value[4] = {written_line, rate, eta, model};
    for (int i = 0; i < 4; i++) {
        Text ml = rtl_block_make(cr, 10, 400, metric_label[i], card.w - 48);
        Text mv = text_make(cr, i == 3 ? FONT_SANS : FONT_MONO, 12, 500,
                            metric_value[i], (int)(card.w - 48), i == 3 ? DIR_RTL : DIR_LTR);
        text_draw_r(cr, &ml, right, y, t->muted);
        text_draw_r(cr, &mv, right, y + 15, t->ink);
        y += 36;
        text_free(&ml); text_free(&mv);
    }

    Text smart = rtl_block_make(cr, 12, 600, "SMART", card.w - 48);
    text_draw_r(cr, &smart, right, y, t->ink); y += smart.h + 8;
    double gap = 7, cell_w = (card.w - 48 - gap) / 2, cell_h = 43;
    const char *health = pending ? smart_he(a->st.smart_verdict) :
                         idle ? smart_he(idle->smart) : "לא דווח";
    smart_cell(cr, t, (Rect){x + cell_w + gap, y, cell_w, cell_h}, "5 · מוקצים מחדש", "—", t->muted);
    smart_cell(cr, t, (Rect){x, y, cell_w, cell_h}, "197 · ממתינים", "—", t->muted);
    y += cell_h + gap;
    smart_cell(cr, t, (Rect){x + cell_w + gap, y, cell_w, cell_h}, "199 · שגיאות CRC", "—", t->muted);
    smart_cell(cr, t, (Rect){x, y, cell_w, cell_h}, "בריאות", health, accent);
    y += cell_h + 10;

    if (d && d->error[0]) {
        Text error = error_make(cr, d->error, card.w - 48);
        text_draw_r(cr, &error, right, y, t->danger);
        text_free(&error);
    } else if (pending && a->st.smart_reason[0]) {
        Text reason = error_make(cr, smart_reason_he(a->st.smart_reason), card.w - 48);
        text_draw_r(cr, &reason, right, y, t->warn);
        text_free(&reason);
    }
    text_free(&name); text_free(&big); text_free(&smart);
}

void screen_cloner(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    double y = head_h + 22;
    cloner_title(cr, t, &a->st, W, &y);

    double gap = 14;
    double buttons_h = a->st.smart_pending ? 54 : 0;
    double bottom = H - N_STATUS_H - 18 - buttons_h;
    double card_w = (W - 2 * N_PAD - 2 * gap) / 3;
    double pct_size = H / W >= .7 ? 64 : N_CLONE_PCT;
    for (int port = 1; port <= 3; port++) {
        Rect card = {W - N_PAD - card_w - (port - 1) * (card_w + gap), y,
                     card_w, bottom - y};
        disk_card(a, cr, card, port, drawer_for_port(&a->st, port),
                  idle_for_port(&a->st, port), pct_size);
    }

    if (a->st.smart_pending) {
        double by = H - N_STATUS_H - 54;
        double bw = 150;
        double right = W - N_PAD;
        int red = !strcmp(a->st.smart_verdict, "failed_last");
        if (!red) {
            action_button(a, cr, (Rect){right - bw, by, bw, 38}, "כתוב בכל זאת",
                          BTN_PRIMARY, HIT_SMART_RESCUE);
            right -= bw + 10;
        }
        action_button(a, cr, (Rect){right - bw, by, bw, 38}, red ? "המשך" : "דלג",
                      BTN_DANGER, HIT_SMART_SKIP);
        right -= bw + 10;
        action_button(a, cr, (Rect){right - bw, by, bw, 38}, "החלף דיסק",
                      BTN_PLAIN, HIT_SMART_REPLACE);
    }
}
