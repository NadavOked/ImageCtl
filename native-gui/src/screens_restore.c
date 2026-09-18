#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"

/* ---- #706: restore a library image onto THIS machine's disk ------------------ */

/* nativeRestore: .restore-layout -- the source box (h3 "מקור", label, the
 * image) and the target box (h3 "יעד", label "דיסק", the disk 18px + its
 * size/device) with .restore-arrow between them; a .native-alert with the
 * erase warning; then the #706 ERASE field. The mockup's progress section
 * belongs to a running restore, which routes to screen_progress. */
static void restore_arrow(cairo_t *cr, Rgb c, double cx, double cy, double w) {
    /* "←" as a vector: shaft from right to left, head at the left */
    cairo_set_line_width(cr, 2);
    cairo_move_to(cr, cx + w / 2, cy); cairo_line_to(cr, cx - w / 2, cy);
    cairo_move_to(cr, cx - w / 2 + w * .3, cy - w * .3); cairo_line_to(cr, cx - w / 2, cy);
    cairo_line_to(cr, cx - w / 2 + w * .3, cy + w * .3);
    draw_set(cr, c); cairo_stroke(cr);
}

void screen_restore(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 2 * N_PAD, in_h = field_height(cr), btn_h = btn_height(cr);

    Head hd = head_make(cr, "משיכת אימג' לכונן המחשב הזה",
                        "אימג' אחד מהספרייה ייכתב על הדיסק של המחשב הזה.", inner);
    double arrow_w = N_CHOICE_ICON + N_ACTION_GAP;                 /* .restore-arrow 26px + room */
    double box_w = (inner - arrow_w - 2 * N_CHOICE_GAP) / 2;
    double box_inner = box_w - 2*N_ROUND_PAD;
    Text h_src = rtl_block_make(cr, N_CLONE_TITLE, 700, "מקור", box_inner);   /* .restore-box h3 */
    Text h_dst = rtl_block_make(cr, N_CLONE_TITLE, 700, "יעד", box_inner);
    Text l1 = label_make(cr, "אימג' ImageCtl", box_inner);
    Text l2 = label_make(cr, "דיסק", box_inner);

    /* Target = the first non-removable local disk. Informational only; the
     * agent rediscovers it before writing (never trusts a UI-supplied device). */
    const Disk *target = NULL;
    for (int i = 0; i < s->ndisks; i++)
        if (!s->disks[i].removable) { target = &s->disks[i]; break; }
    /* #1073: no registered name = nothing to type back = no start. */
    int can_start = (s->nimages > 0) && target != NULL && s->machine_name[0];

    char tname[96], tinfo[120];
    if (target) {
        char sz[32]; fmt_bytes(sz, sizeof sz, (double)target->size_bytes);
        snprintf(tname, sizeof tname, "%s", target->model[0] ? target->model : "דיסק");
        snprintf(tinfo, sizeof tinfo, "%s · %s", sz, target->dev);
    } else {
        snprintf(tname, sizeof tname, "לא נמצא דיסק פנימי");
        snprintf(tinfo, sizeof tinfo, "לא ניתן לשחזר.");
    }
    Text tn = rtl_block_make(cr, N_RESTORE_NAME, 700, tname, box_inner);
    Text tt = rtl_block_make(cr, N_SUB, 400, tinfo, box_inner);
    Text warn = alert_make(cr, "אזהרה: הפעולה תחליף את תוכן הדיסק היעד.", inner);

    Text l3 = { NULL, 0, 0 }, empty = { NULL, 0, 0 };
    double confirm_h = 0, empty_h = 0;
    if (s->nimages == 0) { empty = sub_make(cr, "אין אימג'ים מתאימים למחשב הזה.", inner); empty_h = empty.h + N_ACTION_GAP; }
    else if (!s->machine_name[0]) { empty = sub_make(cr, "למחשב הזה אין שם במרשם השרת — האישור הוא הקלדת השם. רשמו שם בקונסולה.", inner); empty_h = empty.h + N_ACTION_GAP; }
    char l3txt[160];
    snprintf(l3txt, sizeof l3txt, "הקלידו את שם המחשב (%s) לאישור מחיקת הדיסק:", s->machine_name);   /* #1073 */
    if (can_start) { l3 = label_make(cr, l3txt, inner); confirm_h = l3.h + 2 + in_h + N_FORM_GAP; }
    Text err = error_make(cr, s->form_error, inner);
    double err_h = dmax(err.h, ERROR_MIN_H);

    double box_h = 2*N_ROUND_PAD + dmax(h_src.h + N_LABEL_GAP + l1.h + N_LABEL_GAP + in_h,
                                        h_dst.h + N_LABEL_GAP + l2.h + N_LABEL_GAP + tn.h + tt.h);
    double body_h = N_PAD + box_h + N_CHOICE_GAP + alert_height(&warn) + N_ACTION_TOP + empty_h + confirm_h + N_LABEL_GAP + err_h;

    Text labels[2]; int kinds[2], ids[2], nb = 0;
    if (can_start) { labels[nb] = btn_label(cr, "התחל שחזור"); kinds[nb] = BTN_DANGER; ids[nb++] = HIT_RESTORE_START; }
    labels[nb] = btn_label(cr, "חזרה"); kinds[nb] = BTN_PLAIN; ids[nb++] = HIT_BACK;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + N_PAD, y = body.y + N_PAD;

    const char *opts[MAX_IMAGES + 1]; char obuf[MAX_IMAGES][168];
    int nopt = image_options(a, opts, obuf, MAX_IMAGES + 1);
    Rect source = {x + inner - box_w, y, box_w, box_h};
    Rect dest = {x, y, box_w, box_h};
    draw_fill_rrect(cr,source,0,t->choice); draw_border_rrect(cr,source,0,t->round_line,N_BORDER);   /* .restore-box: no radius */
    draw_fill_rrect(cr,dest,0,t->choice);   draw_border_rrect(cr,dest,0,t->round_line,N_BORDER);
    restore_arrow(cr, t->brand_accent, x + box_w + N_CHOICE_GAP + arrow_w / 2, y + box_h / 2, N_CHOICE_ICON);
    double sy = y + N_ROUND_PAD, sx = source.x + N_ROUND_PAD;
    text_draw(cr,&h_src,sx,sy,t->ink);        sy += h_src.h + N_LABEL_GAP;
    text_draw(cr,&l1,sx,sy,t->muted);         sy += l1.h + N_LABEL_GAP;
    Rect sel_r = {sx, sy, box_inner, in_h};
    draw_select(a,cr,sel_r,opts[a->restore_image_sel < nopt ? a->restore_image_sel : 0],HIT_RESTORE_IMAGE);
    double dy = y + N_ROUND_PAD, dx = dest.x + N_ROUND_PAD;
    text_draw(cr,&h_dst,dx,dy,t->ink);        dy += h_dst.h + N_LABEL_GAP;
    text_draw(cr,&l2,dx,dy,t->muted);         dy += l2.h + N_LABEL_GAP;
    text_draw(cr,&tn,dx,dy,target ? t->ink : t->danger); dy += tn.h;
    text_draw(cr,&tt,dx,dy,t->muted);
    y += box_h + N_CHOICE_GAP;
    draw_alert(cr, t, (Rect){ x, y, inner, alert_height(&warn) }, &warn);
    y += alert_height(&warn) + N_ACTION_TOP;
    if (empty.h > 0) { text_draw_r(cr, &empty, x + inner, y, t->muted); y += empty_h; text_free(&empty); }
    if (can_start) {
        text_draw(cr, &l3, x, y, t->muted);                     y += l3.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->restore_confirm, 0, NULL, HIT_RESTORE_CONFIRM);
        y += in_h + N_FORM_GAP;
    }
    text_draw(cr, &err, x, y, t->danger);
    body_clip_end(a, cr);
    foot_draw_bg(cr, t, foot);
    foot_buttons(a, cr, foot, labels, kinds, ids, nb);
    card_end(a, cr);
    if (a->dd_open == HIT_RESTORE_IMAGE) draw_popup(a, cr, sel_r, opts, nopt, a->restore_image_sel);
    text_free(&h_src); text_free(&h_dst); text_free(&l1); text_free(&l2); text_free(&tn); text_free(&tt);
    text_free(&warn); if (can_start) text_free(&l3); text_free(&err);
}
