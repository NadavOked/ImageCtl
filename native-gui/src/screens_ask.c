/* 11/10 (Nadav: "הכול גואי"): the questions the agent used to ask on the
 * text console, as a dialog in the style of the shrink question (#928,
 * screens_shrink.c) -- the approved pattern: the amber frame, a title, the
 * consequences, one filled primary button and a bordered secondary one with
 * a grey line under it. Two questions use it:
 *
 *   growback     a disk left shrunk by an interrupted capture (#926/#929):
 *                grow it back (primary) / leave it (secondary). Was
 *                shrinkoffer.sh:shrink_restore_ask.
 *   capture-end  the end of a capture (#409): power off (primary, the
 *                default) / back to the menu. Was task.sh:capture_end_ask.
 *
 * The agent publishes the question (askgui.sh), guistate.sh copies it into
 * ask_prompt= / ask_row=, and main.c:ask_modal emits ask-<answer>|<nonce>.
 * No answer is the agent's business: it keeps the safe default (leave /
 * power off) when its ceiling passes. Modal: it replaces the hit table. */
#include <stdio.h>
#include "widgets.h"

#define ASK_W_MAX    760.0
#define ASK_PAD      28.0
#define ASK_BTN_H    44.0
#define ASK_GAP      10.0
#define ASK_HINT_GAP 6.0
#define ASK_HINT_PX  12.0

/* The same unit as the shrink dialog: decimal GB, one digit. */
static void ask_gb(char *out, size_t n, unsigned long long b) {
    snprintf(out, n, "%.1f GB", (double)b / 1e9);
}

void draw_ask_dialog(App *a, cairo_t *cr, double W, double H) {
    if (!a->st.ask_pending || a->st.shrink_pending) return;
    const Theme *t = a->theme;
    const int grow = a->st.ask_kind == ASK_GROWBACK;
    double usable = H - N_STATUS_H;
    cairo_rectangle(cr, 0, 0, W, usable);
    draw_set_a(cr, (Rgb){0, 0, 0}, .55);
    cairo_fill(cr);
    a->nhits = 0; a->clip_on = 0;             /* modal: only the answers are clickable */

    double dw = W - 2 * 32 < ASK_W_MAX ? W - 2 * 32 : ASK_W_MAX;
    double inner = dw - 2 * ASK_PAD;
    char title_s[256], lead_s[512];
    const char *primary, *secondary, *hint_s;
    int table_rows = 0;
    for (int i = 0; i < a->st.nask; i++) table_rows += !a->st.ask[i].fs_only;
    if (grow) {
        snprintf(title_s, sizeof title_s, "דיסק %s כווץ לקליטה ולא הוחזר לגודלו", a->st.ask_subject);
        snprintf(lead_s, sizeof lead_s, "%s",
            "קליטה קודמת הופסקה לפני שהמחיצות הוחזרו (הפסקת חשמל או אתחול). ווינדוס עולה בכל מקרה.");
        primary = table_rows ? "החזר לגודל המקורי (מומלץ)" : "מתח את מערכת הקבצים למחיצה (מומלץ)";
        secondary = "השאר כמו שהוא";
        hint_s = "הרשומה נשארת פתוחה בקונסולה, וקליטה מהדיסק הזה תסורב עד שיוחזר";
    } else {
        /* #1470: the machine is released after the upload; the server re-reads every
           file before the image enters the library -- it is not there yet. */
        snprintf(title_s, sizeof title_s, "%s", "הקליטה הסתיימה — השרת מאמת את האימג'");
        snprintf(lead_s, sizeof lead_s, "המחשב יכבה בעוד %s שניות אם לא תבחרו. אחרי הכיבוי אפשר לשלוף את הדיסק בבטחה.",
            a->st.ask_subject);
        primary = "כבה";
        secondary = "חזרה לתפריט";
        hint_s = "המחשב נשאר דלוק ומציג את התפריט — האימג' החדש יופיע להפצה כשהאימות בשרת יעבור";
    }
    Text title = rtl_block_make(cr, 22, 600, title_s, inner);
    Text lead = rtl_block_make(cr, 14, 400, lead_s, inner);
    Text rows[MAX_ASK_ROWS];
    double rows_h = 0;
    for (int i = 0; i < a->st.nask; i++) {
        char now_s[24], orig_s[24], line[320];
        ask_gb(now_s, sizeof now_s, a->st.ask[i].now_bytes);
        ask_gb(orig_s, sizeof orig_s, a->st.ask[i].orig_bytes);
        if (a->st.ask[i].fs_only)
            snprintf(line, sizeof line, "מחיצה %d: מערכת הקבצים (%s) קטנה מהמחיצה (%s)",
                     a->st.ask[i].idx, now_s, orig_s);
        else
            snprintf(line, sizeof line, "מחיצה %d: עכשיו %s, במקור %s", a->st.ask[i].idx, now_s, orig_s);
        rows[i] = rtl_block_make(cr, 15, 500, line, inner);
        rows_h += rows[i].h + 6;
    }
    Text hint = text_make(cr, FONT_SANS, ASK_HINT_PX, 400, hint_s, (int)inner, DIR_RTL);
    pango_layout_set_alignment(hint.layout, PANGO_ALIGN_CENTER);
    double answers_h = ASK_BTN_H + ASK_GAP + ASK_BTN_H + ASK_HINT_GAP + hint.h;
    double dh = ASK_PAD + title.h + 8 + lead.h + 18 + rows_h + (a->st.nask ? 18 : 0) + answers_h + ASK_PAD;
    if (dh > usable - 24) dh = usable - 24;   /* MAX_ASK_ROWS keeps this from clipping on 768 */
    Rect box = { (W - dw) / 2, (usable - dh) / 2, dw, dh };
    draw_box_shadow(cr, box, t->radius, 10, 30, -8, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, box, t->radius, t->surface);
    draw_border_rrect(cr, box, t->radius, t->warn, 2);

    double right = box.x + box.w - ASK_PAD, y = box.y + ASK_PAD;
    text_draw_r(cr, &title, right, y, t->ink); y += title.h + 8;
    text_draw_r(cr, &lead, right, y, t->muted); y += lead.h + 18;
    for (int i = 0; i < a->st.nask; i++) {
        text_draw_r(cr, &rows[i], right, y, t->ink);
        y += rows[i].h + 6;
        text_free(&rows[i]);
    }
    y = box.y + box.h - ASK_PAD - answers_h;
    Text l = btn_label(cr, primary);
    draw_btn(a, cr, (Rect){ box.x + ASK_PAD, y, inner, ASK_BTN_H }, &l, BTN_PRIMARY, HIT_ASK_PRIMARY);
    text_free(&l);
    y += ASK_BTN_H + ASK_GAP;
    l = btn_label(cr, secondary);
    draw_btn(a, cr, (Rect){ box.x + ASK_PAD, y, inner, ASK_BTN_H }, &l, BTN_PLAIN, HIT_ASK_SECONDARY);
    text_free(&l);
    y += ASK_BTN_H + ASK_HINT_GAP;
    text_draw(cr, &hint, box.x + ASK_PAD, y, t->muted);
    text_free(&title); text_free(&lead); text_free(&hint);
}
