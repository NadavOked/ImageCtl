/* The capture path: #st-pick (the disks and the form), #st-progress,
 * #st-done -- and #st-message, the card for "nothing to do here". Text is
 * index.html's / station.js's; numbers are station.css / console.css. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "widgets.h"

/* ---- #st-pick -------------------------------------------------------------- */

/* The options of #st-folder: "ללא תיקייה" then the folders (loadFolders). */
static int folder_options(const App *a, const char **out, int max) {
    int n = 0;
    out[n++] = "ללא תיקייה";
    for (int i = 0; i < a->st.nfolders && n < max; i++) out[n++] = a->st.folders[i];
    return n;
}

void screen_pick(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 44;
    Head hd = head_make(cr, "קליטת אימג' חדש", "מה מותקן עכשיו — בחרו את כונן המקור.", inner);

    /* drawDisks: internal disks only (removable filtered out) */
    int idx[MAX_DISKS], n = 0;
    for (int i = 0; i < s->ndisks; i++) if (!s->disks[i].removable) idx[n++] = i;

    /* .disk-card: padding 16, gap 14, border 1, radius 12, margin-bottom 10;
     * tray 52x34 at the right, <b> 14.5px 700, <small> 12.5 muted, .dev mono
     * pushed to the inline end (left). */
    Text db[MAX_DISKS], ds[MAX_DISKS], dd[MAX_DISKS];
    double dh[MAX_DISKS], text_w[MAX_DISKS];
    double body_h = 22;
    Text none = { NULL, 0, 0 };
    if (n == 0) {
        none = sub_make(cr, "עוד לא דווחו כוננים — המידע מגיע כשהמחשב עולה ב-PXE.", inner);
        body_h += none.h;
    }
    for (int k = 0; k < n; k++) {
        const Disk *d = &s->disks[idx[k]];
        char size[32], small[96];
        fmt_bytes(size, sizeof size, (double)d->size_bytes);
        snprintf(small, sizeof small, "%s · %s", size, d->has_data ? "יש מערכת על הכונן" : "ריק");
        dd[k] = text_make(cr, FONT_MONO, 14, 400, d->dev, 0, DIR_LTR);
        text_w[k] = inner - 16 - 52 - 14 - 14 - dd[k].w - 16;
        db[k] = text_make(cr, FONT_SANS, 14.5, 700, d->model[0] ? d->model : "כונן", (int)text_w[k], DIR_RTL);
        ds[k] = text_make(cr, FONT_SANS, 12.5, 400, small, (int)text_w[k], DIR_RTL);
        dh[k] = 16 + dmax(34, db[k].h + ds[k].h) + 16 + 2;
        body_h += dh[k] + 10;
    }

    /* #st-form shows once a disk is chosen (drawDisks click handler) */
    int form = a->chosen_disk >= 0;
    Text l1 = { NULL, 0, 0 }, l2 = l1, l3 = l1, nf = l1, err = l1;
    double in_h = field_height(cr), btn_h = btn_height(cr), err_h = 0;
    if (form) {
        l1 = label_make(cr, "שם האימג'", inner);
        l2 = label_make(cr, "תיאור (לא חובה)", inner);
        l3 = label_make(cr, "תיקיית יעד", inner);
        nf = btn_label(cr, "+ חדשה");
        err = error_make(cr, s->form_error, inner);
        err_h = dmax(err.h, ERROR_MIN_H);
        /* last disk card's margin-bottom (10) collapses with the label's
         * margin-top (6); an input inside its label keeps 14 + 6 below it;
         * the folder row is select (margin-bottom 14) + 6 to .error. */
        body_h += l1.h + 2 + in_h + 20 + l2.h + 2 + in_h + 20 + l3.h + 6 + in_h + 14
                + (a->newfolder_shown ? in_h + 14 : 0) + 6 + err_h;
    }
    body_h += 22;

    /* .sfoot: start (when chosen), back, and the hint pushed to the left */
    Text bs = form ? btn_label(cr, "התחל קליטה") : (Text){ NULL, 0, 0 };
    Text bb = btn_label(cr, "חזרה");
    double btns_w = btn_width(&bb) + (form ? 8 + btn_width(&bs) : 0);
    Text hint = sub_make(cr, "הקליטה קוראת רק בלוקים תפוסים ואינה משנה את הכונן.", inner - btns_w - 8);
    double foot_h = foot_height(cr, dmax(btn_h, hint.h));

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_h, &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + 22, y = body.y + 22;
    if (n == 0) { text_draw(cr, &none, x, y, t->ink); y += none.h; text_free(&none); }
    for (int k = 0; k < n; k++) {
        Rect c = { x, y, inner, dh[k] };
        int sel = a->chosen_disk == idx[k], hov = hovered(a, c);
        draw_fill_rrect(cr, c, 12, sel ? t->indigo_soft : t->field);
        draw_border_rrect(cr, c, 12, sel || hov ? t->indigo : t->hair, 1);
        double tx = c.x + c.w - 16 - 52;
        draw_tray(cr, t, tx, c.y + (c.h - 34) / 2, 0);
        double block = db[k].h + ds[k].h, by = c.y + (c.h - block) / 2;
        text_draw(cr, &db[k], tx - 14 - text_w[k], by, t->ink);
        text_draw(cr, &ds[k], tx - 14 - text_w[k], by + db[k].h, t->muted);
        text_draw(cr, &dd[k], c.x + 16, c.y + (c.h - dd[k].h) / 2, t->muted);
        hit_add(a, c, HIT_DISK_BASE + idx[k]);
        text_free(&db[k]); text_free(&ds[k]); text_free(&dd[k]);
        y += dh[k] + 10;
    }

    Rect sel_r = { 0, 0, 0, 0 };
    if (form) {
        text_draw(cr, &l1, x, y, t->muted);                 y += l1.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->name, 0, "למשל: Office 2024 — סטנדרט", HIT_NAME);
        y += in_h + 20;
        text_draw(cr, &l2, x, y, t->muted);                 y += l2.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->desc, 0, NULL, HIT_DESC);
        y += in_h + 20;
        text_draw(cr, &l3, x, y, t->muted);                 y += l3.h + 6;
        /* .folder-row: flex, gap 8, align-items:center; select flex:1. The
         * select keeps its margin-bottom 14 inside the row, so the row is
         * that much taller and the button is centred against it. */
        const char *opts[MAX_FOLDERS + 1];
        int nopt = folder_options(a, opts, MAX_FOLDERS + 1);
        double bw = btn_width(&nf), row_h = in_h + 14;
        sel_r = (Rect){ x + bw + 8, y, inner - bw - 8, in_h };
        draw_select(a, cr, sel_r, opts[a->folder_sel < nopt ? a->folder_sel : 0], HIT_FOLDER);
        draw_btn(a, cr, (Rect){ x, y + (row_h - btn_h) / 2, bw, btn_h }, &nf, BTN_PLAIN, HIT_NEWFOLDER);
        y += row_h;
        if (a->newfolder_shown) {
            draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->folder_new, 0, "שם התיקייה החדשה", HIT_FOLDER_NEW);
            y += in_h + 14;
        }
        y += 6;
        text_draw(cr, &err, x, y, t->danger);
        text_free(&l1); text_free(&l2); text_free(&l3); text_free(&nf); text_free(&err);
    }
    body_clip_end(a, cr);

    foot_draw_bg(cr, t, foot);
    double bx = foot.x + foot.w - 22, byy = foot.y + 1 + 14;
    if (form) {
        bx -= btn_width(&bs);
        draw_btn(a, cr, (Rect){ bx, byy, btn_width(&bs), btn_h }, &bs, BTN_PRIMARY, HIT_START);
        bx -= 8;
        text_free(&bs);
    }
    bx -= btn_width(&bb);
    draw_btn(a, cr, (Rect){ bx, byy, btn_width(&bb), btn_h }, &bb, BTN_PLAIN, HIT_BACK);
    text_free(&bb);
    /* .st-card .sfoot{align-items:center} .sub{margin-inline-start:auto} */
    text_draw(cr, &hint, foot.x + 22, foot.y + 1 + 14 + (dmax(btn_h, hint.h) - hint.h) / 2, t->ink);
    text_free(&hint);

    card_end(a, cr);
    /* the open <select> list: after card_end, so the card's clip does not
     * cut it, and last, so it is on top of everything */
    if (a->dd_open == HIT_FOLDER && form) {
        const char *opts[MAX_FOLDERS + 1];
        int nopt = folder_options(a, opts, MAX_FOLDERS + 1);
        draw_popup(a, cr, sel_r, opts, nopt, a->folder_sel);
    }
}

/* ---- #st-progress ----------------------------------------------------------- */

void screen_progress(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 44;

    /* drawProgress, unless the agent set the texts itself (a cloning
     * machine or a class receiver shows this card with no operator). */
    char title[160], sub[224], pct[96], bytes[64] = "";
    if (s->title[0])              snprintf(title, sizeof title, "%s", s->title);
    else if (s->task_direct)      snprintf(title, sizeof title, "משדר מהדיסק %s למחשבי השיכפול", s->task_disk);   /* #715 */
    else if (s->task_name[0])     snprintf(title, sizeof title, "קולט: %s", s->task_name);
    else                          snprintf(title, sizeof title, "קולט…");          /* index.html default */
    if (s->sub[0])                snprintf(sub, sizeof sub, "%s", s->sub);
    else if (s->task == TASK_PENDING) snprintf(sub, sizeof sub, "ממתין לסוכן — ודאו שהמחשב עלה ב-PXE");
    else if (s->task_disk[0])     snprintf(sub, sizeof sub, "כונן המקור: %s", s->task_disk);
    else                          sub[0] = 0;
    progress_label(pct, sizeof pct, s->pct, s->moving, s->partition);
    if (s->bytes) { char b[32]; fmt_bytes(b, sizeof b, (double)s->bytes); snprintf(bytes, sizeof bytes, "%s נקראו", b); }

    Head hd = head_make(cr, title, sub, inner);
    Text tp = text_make(cr, FONT_SANS, 22, 700, pct, 0, DIR_RTL);          /* .st-prog-line b */
    Text tb = sub_make(cr, bytes, inner / 2);                               /* span.sub#st-bytes */
    Text note = sub_make(cr, "אל תכבו את המחשב. אפשר לעקוב גם מהקונסולה.", inner);
    /* align-items:baseline: the small text hangs from the big one's baseline */
    double drop = text_baseline(&tp) - text_baseline(&tb);
    double line_h = dmax(tp.h, drop + tb.h);
    /* .big-bar margin 6px 0 10px */
    double body_h = 22 + 6 + 14 + 10 + line_h + note.h + 22;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, 0, &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + 22, y = body.y + 22 + 6;
    draw_big_bar(cr, t, (Rect){ x, y, inner, 14 }, s->pct, s->moving);
    y += 14 + 10;
    text_draw_r(cr, &tp, x + inner, y, t->ink);                            /* justify-content:space-between */
    if (bytes[0]) text_draw(cr, &tb, x, y + drop, t->ink);
    y += line_h;
    text_draw(cr, &note, x, y, t->ink);
    body_clip_end(a, cr);
    card_end(a, cr);
    text_free(&tp); text_free(&tb); text_free(&note);
}

/* ---- #st-done ---------------------------------------------------------------- */

void screen_done(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    double inner = card_width(W) - 44;
    Head hd = head_make(cr, a->done_title, a->done_sub, inner);
    Text bt = btn_label(cr, "קליטה נוספת");
    double btn_h = btn_height(cr), foot_h = foot_height(cr, btn_h);
    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, 0, foot_h, &body, &foot);
    foot_draw_bg(cr, t, foot);
    double bw = btn_width(&bt);
    draw_btn(a, cr, (Rect){ foot.x + foot.w - 22 - bw, foot.y + 1 + 14, bw, btn_h }, &bt, BTN_PLAIN, HIT_AGAIN);
    card_end(a, cr);
    text_free(&bt);
}

/* ---- #st-message ------------------------------------------------------------- */

void screen_message(App *a, cairo_t *cr, double W, double H, double head_h) {
    double inner = card_width(W) - 44;
    Head hd = head_make(cr, a->st.msg_title, a->st.msg_sub, inner);
    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, 0, 0, &body, &foot);
    card_end(a, cr);
}
