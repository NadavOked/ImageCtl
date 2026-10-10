/* #928: the capture's shrink question as a dialog over the build screens.
 * Until #928 the kiosk was stopped for it and the question was asked on the
 * text console (shrinkplan.sh:shrink_ask). The agent now publishes the plan
 * (shrinkgui.sh), guistate.sh copies it into shrink_prompt= and
 * shrink_partition=, and this draws one table with the three answers of the
 * text menu. The dialog is modal: it replaces the hit table, so nothing
 * behind it can be clicked while the question is open. */
#include <stdio.h>
#include "widgets.h"

#define SHRINK_W_MAX 760.0
#define SHRINK_PAD   28.0
#define SHRINK_BTN_H 44.0
#define SHRINK_GAP   10.0

static const struct { const char *label; int kind, id; } SHRINK_BTN[3] = {
    { "כווץ וקלוט", BTN_PRIMARY, HIT_SHRINK_CONTINUE },
    { "קלוט בלי כיווץ (האימג' לא ייכנס לדיסקים קטנים יותר)", BTN_PLAIN, HIT_SHRINK_PLAIN },
    { "בטל קליטה", BTN_PLAIN, HIT_SHRINK_CANCEL },
};

/* The same unit as the text screen (shrinkplan.sh:_shrink_gb): decimal GB,
 * one digit -- the operator sees one number for one partition everywhere. */
static void shrink_gb(char *out, size_t n, unsigned long long b) {
    snprintf(out, n, "%.1f GB", (double)b / 1e9);
}

void draw_shrink_dialog(App *a, cairo_t *cr, double W, double H) {
    if (!a->st.shrink_pending) return;
    const Theme *t = a->theme;
    double usable = H - N_STATUS_H;
    cairo_rectangle(cr, 0, 0, W, usable);
    draw_set_a(cr, (Rgb){0, 0, 0}, .55);
    cairo_fill(cr);
    a->nhits = 0; a->clip_on = 0;             /* modal: only the answers are clickable */

    double dw = W - 2 * 32 < SHRINK_W_MAX ? W - 2 * 32 : SHRINK_W_MAX;
    double inner = dw - 2 * SHRINK_PAD;
    Text title = rtl_block_make(cr, 22, 600, "הקליטה תכווץ מחיצות במחשב הזה", inner);
    Text lead = rtl_block_make(cr, 14, 400,
        "המחיצות מוחזרות לגודלן כשהקליטה מסתיימת. ווינדוס עולה בכל מקרה.", inner);
    Text rows[MAX_SHRINK_PARTS];
    double rows_h = 0;
    for (int i = 0; i < a->st.nshrink; i++) {
        char from[24], to[24], line[96];
        shrink_gb(from, sizeof from, a->st.shrink[i].old_bytes);
        shrink_gb(to, sizeof to, a->st.shrink[i].new_bytes);
        snprintf(line, sizeof line, "מחיצה %d: מ-%s ל-%s", a->st.shrink[i].idx, from, to);
        rows[i] = rtl_block_make(cr, 15, 500, line, inner);
        rows_h += rows[i].h + 6;
    }
    double dh = SHRINK_PAD + title.h + 8 + lead.h + 18 + rows_h + 18 +
                3 * SHRINK_BTN_H + 2 * SHRINK_GAP + SHRINK_PAD;
    if (dh > usable - 24) dh = usable - 24;   /* MAX_SHRINK_PARTS keeps this from clipping on 768 */
    Rect box = { (W - dw) / 2, (usable - dh) / 2, dw, dh };
    draw_box_shadow(cr, box, t->radius, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, box, t->radius, t->surface);
    draw_border_rrect(cr, box, t->radius, t->warn, 2);

    double right = box.x + box.w - SHRINK_PAD, y = box.y + SHRINK_PAD;
    text_draw_r(cr, &title, right, y, t->ink); y += title.h + 8;
    text_draw_r(cr, &lead, right, y, t->muted); y += lead.h + 18;
    for (int i = 0; i < a->st.nshrink; i++) {
        text_draw_r(cr, &rows[i], right, y, t->ink);
        y += rows[i].h + 6;
        text_free(&rows[i]);
    }
    y = box.y + box.h - SHRINK_PAD - 3 * SHRINK_BTN_H - 2 * SHRINK_GAP;
    for (int k = 0; k < 3; k++) {
        Text l = btn_label(cr, SHRINK_BTN[k].label);
        draw_btn(a, cr, (Rect){ box.x + SHRINK_PAD, y, inner, SHRINK_BTN_H }, &l,
                 SHRINK_BTN[k].kind, SHRINK_BTN[k].id);
        text_free(&l);
        y += SHRINK_BTN_H + SHRINK_GAP;
    }
    text_free(&title); text_free(&lead);
}
