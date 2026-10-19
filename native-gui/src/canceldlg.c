/* #1386 (Nadav 17/10): "בטל דיסק זה" -- the window, after the approved mockup
 * (docs/design/native/cancel-drawer-1386/mockup.html, PR #1617): the disk and
 * where it is, what happens (red: the write stops now and the partition table
 * is wiped; green: the other drawers carry on, the next round writes it as a
 * green one), "המשך לכתוב" (Esc) and the red "בטל את דיסק N" (Del -- never
 * Enter, so a double press does not wipe a disk). No typed word (#1270). On
 * the build machine X opens it with the choice among the writing drawers.
 *
 * The pieces are the local clone's dialog (lc_dialog_box, lc_btn), like the
 * SMART window (smartdlg.c), so it looks like the other windows in both themes. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "canceldlg.h"
#include "lcui.h"
#include "screens_rounds.h"

static int picks_now(const App *a, int build, CancelPick *out) {
    return build ? room_cancel_picks(a, out, MAX_CANCEL_PICKS) : cloner_cancel_picks(a, out, MAX_CANCEL_PICKS);
}

int cdlg_open(App *a, int build, int sel, int picker) {
    CancelDlg *d = &a->cdlg;
    memset(d, 0, sizeof *d);
    d->n = picks_now(a, build, d->picks);
    if (d->n <= 0 || sel < 0 || sel >= d->n) { d->n = 0; return 0; }
    d->open = 1; d->build = build; d->sel = sel; d->picker = picker && d->n > 1;
    return 1;
}

void cdlg_after_reload(App *a) {
    CancelDlg *d = &a->cdlg;
    if (!d->open) return;
    CancelPick now[MAX_CANCEL_PICKS]; char mac[24], dev[32];
    snprintf(mac, sizeof mac, "%s", d->picks[d->sel].mac); snprintf(dev, sizeof dev, "%s", d->picks[d->sel].dev);
    int n = picks_now(a, d->build, now), at = -1;
    for (int i = 0; i < n; i++) if (!strcmp(now[i].mac, mac) && !strcmp(now[i].dev, dev)) at = i;
    if (at < 0) { d->open = 0; return; }           /* it finished, failed or is already cancelled */
    memcpy(d->picks, now, sizeof now); d->n = n; d->sel = at;
}

int cdlg_handle(App *a, const Event *e, CancelPick *done) {
    CancelDlg *d = &a->cdlg;
    memset(done, 0, sizeof *done);
    if (!d->open) return 0;
    if (e->type == UIEV_MOVE) return 0;
    int go = -1;
    if (e->type == UIEV_KEY) {
        if (e->key == KEYSYM_ESC) go = 0;
        else if (e->key == KEYSYM_DELETE) go = 1;
        else if (d->picker && (e->key == KEYSYM_UP || e->key == KEYSYM_DOWN))
            d->sel = (d->sel + (e->key == KEYSYM_DOWN ? 1 : d->n - 1)) % d->n;
    } else if (e->type == UIEV_CLICK) {
        int id = app_hit(a, e->x, e->y);
        if (id == HIT_CDLG_KEEP) go = 0;
        else if (id == HIT_CDLG_GO) go = 1;
        else if (id >= HIT_CDLG_PICK_BASE && id < HIT_CDLG_PICK_BASE + d->n) d->sel = id - HIT_CDLG_PICK_BASE;
    }
    if (go >= 0) {
        if (go) *done = d->picks[d->sel];
        d->open = 0;
    }
    return 1;
}

/* ---- drawing ------------------------------------------------------------------- */

#define CD_IMP 4

void cdlg_draw(App *a, cairo_t *cr, double W, double H) {
    const CancelDlg *d = &a->cdlg;
    if (!d->open) return;
    const Theme *t = a->theme;
    const CancelPick *p = &d->picks[d->sel];
    double w = fmin(560, W - 64), inner = w - 48;

    char label_i[sizeof p->label + 8], title[sizeof label_i + 64], sub[400], model_i[100], size_i[48], go[sizeof p->label + 32];
    bidi_isolate(p->label, label_i, sizeof label_i);
    bidi_isolate(p->model[0] ? p->model : "דגם לא דווח", model_i, sizeof model_i);
    bidi_isolate(p->size[0] ? p->size : "—", size_i, sizeof size_i);
    snprintf(title, sizeof title, "לבטל את הכתיבה ל%s?", label_i);
    if (p->pct >= 0) snprintf(sub, sizeof sub, "%s · %s · %s · עכשיו ב-%d%%", p->where, model_i, size_i, p->pct);
    else snprintf(sub, sizeof sub, "%s · %s · %s", p->where, model_i, size_i);
    snprintf(go, sizeof go, "בטל את %s", p->label);
    static const char *imp[CD_IMP] = {
        "<b>הכתיבה לדיסק הזה נעצרת מיד.</b> מה שכבר נכתב — נזרק.",
        "<b>טבלת המחיצות שלו נמחקת</b> והוא יוצג ריק, בלי מערכת. אי אפשר לבטל את זה.",
        "<b>שאר המגירות ממשיכות</b> באותו זרם, בלי עצירה.",
        "בסבב הבא הדיסק ייבחר ככתיבה רגילה (ירוק) — זה לא נחשב כשל." };

    /* measure: head, sub, picker, impacts, foot */
    Text h = text_make(cr, FONT_SANS, 20, 600, title, (int)inner, DIR_RTL);
    Text s = text_make(cr, FONT_SANS, 13, 400, sub, (int)inner, DIR_RTL);
    Text im[CD_IMP]; double imp_h = 0;
    for (int i = 0; i < CD_IMP; i++) {
        im[i] = text_make_markup(cr, FONT_SANS, 14, 400, imp[i], (int)(inner - 20), DIR_RTL);
        imp_h += im[i].h + (i ? 9 : 0);
    }
    double row_h = 38, pick_h = d->picker ? d->n * row_h + 16 : 0, bh = 40;
    double dh = 20 + h.h + 3 + s.h + 10 + 4 + pick_h + imp_h + 20 + 1 + 14 + bh + 14;
    Rect box = lc_dialog_box(a, cr, W, H, w, dh);       /* the dim, the shadow, the modal hit table */
    double right = box.x + box.w - 24, y = box.y + 20;
    text_draw_r(cr, &h, right, y, t->ink); y += h.h + 3;
    text_draw_r(cr, &s, right, y, t->muted); y += s.h + 14;
    text_free(&h); text_free(&s);

    if (d->picker) {                                   /* X: the writers, the chosen one marked */
        Rect pr = { box.x + 24, y, inner, d->n * row_h };
        draw_border_rrect(cr, pr, t->radius_sm, t->hair, 1);
        for (int i = 0; i < d->n; i++) {
            Rect r = { pr.x, pr.y + i * row_h, pr.w, row_h };
            int on = i == d->sel;
            if (on) draw_fill_rrect(cr, r, t->radius_sm, t->danger_soft);
            else if (i) { cairo_rectangle(cr, r.x, r.y, r.w, 1); draw_set(cr, t->hair); cairo_fill(cr); }
            double cx = r.x + r.w - 20, cy = r.y + row_h / 2;
            cairo_arc(cr, cx, cy, 7, 0, 2 * M_PI); draw_set(cr, on ? t->danger : t->hair);
            cairo_set_line_width(cr, 2); cairo_stroke(cr);
            if (on) { cairo_arc(cr, cx, cy, 3.5, 0, 2 * M_PI); draw_set(cr, t->danger); cairo_fill(cr); }
            char row[400], lab_i[sizeof p->label + 8], pct[16];
            bidi_isolate(d->picks[i].label, lab_i, sizeof lab_i);
            snprintf(row, sizeof row, "%s · %s", d->picks[i].where, lab_i);
            snprintf(pct, sizeof pct, "%d%%", d->picks[i].pct < 0 ? 0 : d->picks[i].pct);
            Text tr = text_make(cr, FONT_SANS, 13, on ? 600 : 400, row, (int)(r.w - 110), DIR_RTL);
            Text tp = text_make(cr, FONT_MONO, 12, 400, pct, 0, DIR_LTR);
            text_draw_r(cr, &tr, cx - 16, cy - tr.h / 2, t->ink);
            text_draw(cr, &tp, r.x + 14, cy - tp.h / 2, t->muted);
            text_free(&tr); text_free(&tp);
            hit_add(a, r, HIT_CDLG_PICK_BASE + i);
        }
        y += d->n * row_h + 16;
    }

    for (int i = 0; i < CD_IMP; i++) {                 /* .imp: red = lost, green = not touched */
        cairo_arc(cr, right - 5, y + 10, 5, 0, 2 * M_PI); draw_set(cr, i < 2 ? t->danger : t->led_ok); cairo_fill(cr);
        text_draw_r(cr, &im[i], right - 20, y, t->ink); y += im[i].h + 9;
        text_free(&im[i]);
    }
    y += 11;
    cairo_rectangle(cr, box.x, y, box.w, 1); draw_set(cr, t->hair); cairo_fill(cr); y += 14;

    /* the foot, from the inline end: "המשך לכתוב" (Esc), then the red one (Del) */
    double x = right;
    x -= lc_btn(a, cr, x, y, bh, "המשך לכתוב", "Esc", BTN_PLAIN, HIT_CDLG_KEEP, 0) + 10;
    x -= lc_btn(a, cr, x, y, bh, go, "Del", BTN_DANGER, HIT_CDLG_GO, 0) + 12;
    Text fm = text_make(cr, FONT_SANS, 12, 400, "הגל ממשיך. רק הדיסק הזה יוצא.", (int)fmax(10, x - box.x - 24), DIR_RTL);
    text_draw_r(cr, &fm, x, y + (bh - fm.h) / 2, t->muted); text_free(&fm);
}
