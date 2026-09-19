#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"

/* #706 / #1090: the destructive local restore is deliberately a single,
 * visible page. Images are cards, not a transient select popup, so the
 * operator can see the source and the physical target at the same time. */

static void restore_arrow(cairo_t *cr, Rgb c, double cx, double cy, double w) {
    /* RTL reading order: source is on the right, target on the left. */
    cairo_set_line_width(cr, 2);
    cairo_move_to(cr, cx + w / 2, cy);
    cairo_line_to(cr, cx - w / 2, cy);
    cairo_move_to(cr, cx - w / 2 + w * .28, cy - w * .28);
    cairo_line_to(cr, cx - w / 2, cy);
    cairo_line_to(cr, cx - w / 2 + w * .28, cy + w * .28);
    draw_set(cr, c);
    cairo_stroke(cr);
}

static void restore_title(cairo_t *cr, const Theme *t, double W, double *y) {
    Text h = rtl_block_make(cr, N_TITLE, 600, "שחזור אימג' למחשב הזה", W - 2 * N_PAD);
    Text p = rtl_block_make(cr, 13, 400,
        "בוחרים מקור, בודקים את הדיסק הפנימי ומקלידים את שם המחשב לאישור.",
        W - 2 * N_PAD);
    text_draw_r(cr, &h, W - N_PAD, *y, t->ink);
    *y += h.h + 4;
    text_draw_r(cr, &p, W - N_PAD, *y, t->muted);
    *y += p.h + 12;
    cairo_rectangle(cr, N_PAD, *y, W - 2 * N_PAD, 1);
    draw_set(cr, t->hair);
    cairo_fill(cr);
    *y += 18;
    text_free(&h);
    text_free(&p);
}

static void disabled_button(cairo_t *cr, const Theme *t, Rect r, Text *label) {
    draw_fill_rrect(cr, r, t->radius, t->field);
    draw_border_rrect(cr, r, t->radius, t->hair, 1);
    text_draw(cr, label, r.x + (r.w - label->w) / 2,
              r.y + (r.h - label->h) / 2, t->muted);
}

void screen_restore(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double y = head_h + 22;
    restore_title(cr, t, W, &y);

    const Disk *target = NULL;
    for (int i = 0; i < s->ndisks; i++) {
        if (!s->disks[i].removable) {
            target = &s->disks[i];
            break;
        }
    }

    double avail_w = W - 2 * N_PAD;
    double arrow_w = 58;
    double panel_gap = 14;
    double panel_w = (avail_w - arrow_w - 2 * panel_gap) / 2;
    double panel_h = 150;
    Rect source = {W - N_PAD - panel_w, y, panel_w, panel_h};
    Rect dest = {N_PAD, y, panel_w, panel_h};
    draw_fill_rrect(cr, source, t->radius, t->surface);
    draw_border_rrect(cr, source, t->radius, t->hair, 1);
    draw_fill_rrect(cr, dest, t->radius, t->surface);
    draw_border_rrect(cr, dest, t->radius, target ? t->hair : t->danger, 1);
    restore_arrow(cr, t->indigo, N_PAD + panel_w + panel_gap + arrow_w / 2,
                  y + panel_h / 2, 30);

    double pad = 22;
    Text sh = rtl_block_make(cr, 15, 600, "מקור", source.w - 2 * pad);
    Text sl = rtl_block_make(cr, 12, 400, "ספריית ImageCtl", source.w - 2 * pad);
    const char *selected = "טרם נבחר אימג'";
    if (a->restore_image_sel > 0 && a->restore_image_sel <= s->nimages)
        selected = s->images[a->restore_image_sel - 1].name;
    Text sv = rtl_block_make(cr, 18, 600, selected, source.w - 2 * pad);
    double sy = source.y + pad;
    text_draw_r(cr, &sh, source.x + source.w - pad, sy, t->ink); sy += sh.h + 10;
    text_draw_r(cr, &sl, source.x + source.w - pad, sy, t->muted); sy += sl.h + 8;
    text_draw_r(cr, &sv, source.x + source.w - pad, sy,
                a->restore_image_sel ? t->ink : t->muted);

    char target_name[128], target_info[128];
    if (target) {
        char size[32];
        fmt_bytes(size, sizeof size, (double)target->size_bytes);
        snprintf(target_name, sizeof target_name, "%s",
                 target->model[0] ? target->model : "דיסק פנימי");
        snprintf(target_info, sizeof target_info, "%s · %s", size, target->dev);
    } else {
        snprintf(target_name, sizeof target_name, "לא נמצא דיסק פנימי");
        snprintf(target_info, sizeof target_info, "השחזור חסום");
    }
    Text dh = rtl_block_make(cr, 15, 600, "יעד", dest.w - 2 * pad);
    Text dl = rtl_block_make(cr, 12, 400, "הדיסק המקומי", dest.w - 2 * pad);
    Text dv = rtl_block_make(cr, 18, 600, target_name, dest.w - 2 * pad);
    Text di = text_make(cr, FONT_MONO, 12, 400, target_info, 0, DIR_LTR);
    double dy = dest.y + pad;
    text_draw_r(cr, &dh, dest.x + dest.w - pad, dy, t->ink); dy += dh.h + 10;
    text_draw_r(cr, &dl, dest.x + dest.w - pad, dy, t->muted); dy += dl.h + 8;
    text_draw_r(cr, &dv, dest.x + dest.w - pad, dy, target ? t->ink : t->danger);
    dy += dv.h + 5;
    text_draw_r(cr, &di, dest.x + dest.w - pad, dy, t->muted);

    y += panel_h + 14;
    Text warning = alert_make(cr,
        "אזהרה: השחזור יחליף את כל תוכן דיסק היעד. אי־אפשר לבטל את הפעולה לאחר שהחלה.",
        avail_w);
    draw_alert(cr, t, (Rect){N_PAD, y, avail_w, alert_height(&warning)}, &warning);
    y += alert_height(&warning) + 17;

    Text gh = rtl_block_make(cr, 15, 600, "בחירת אימג'", avail_w);
    text_draw_r(cr, &gh, W - N_PAD, y, t->ink);
    y += gh.h + 10;
    int cols = H / W >= .7 ? 1 : 2;
    double grid_gap = 14;
    double image_w = (avail_w - (cols - 1) * grid_gap) / cols;
    double image_h = 56;
    int shown = s->nimages;
    double grid_bottom = H - N_STATUS_H - 128;
    for (int i = 0; i < shown; i++) {
        int row = i / cols, col = i % cols;
        Rect box = {W - N_PAD - image_w - col * (image_w + grid_gap),
                    y + row * (image_h + grid_gap), image_w, image_h};
        if (box.y + box.h > grid_bottom) break;
        int selected_now = a->restore_image_sel == i + 1;
        draw_fill_rrect(cr, box, t->radius,
                        selected_now ? t->indigo_soft : t->surface);
        draw_border_rrect(cr, box, t->radius,
                          selected_now ? t->indigo : t->hair,
                          selected_now ? 2 : 1);
        Text name = rtl_block_make(cr, 14, 600, s->images[i].name, box.w - 32);
        Text folder = rtl_block_make(cr, 11, 400,
                                     s->images[i].folder[0] ? s->images[i].folder : "ללא תיקייה",
                                     box.w - 32);
        text_draw_r(cr, &name, box.x + box.w - 16, box.y + 10, t->ink);
        text_draw_r(cr, &folder, box.x + box.w - 16, box.y + 31, t->muted);
        if (i < 9) {
            char key[4]; snprintf(key, sizeof key, "%d", i + 1);
            Text kt = text_make(cr, FONT_MONO, 11, 500, key, 0, DIR_LTR);
            Rect kb = {box.x + 10, box.y + 10, kt.w + 14, 21};
            draw_fill_rrect(cr, kb, 3, t->field);
            draw_border_rrect(cr, kb, 3, t->hair, 1);
            text_draw(cr, &kt, kb.x + 7, kb.y + (kb.h - kt.h) / 2, t->ink);
            text_free(&kt);
        }
        text_free(&name);
        text_free(&folder);
        hit_add(a, box, HIT_OPTION_BASE + i);
    }
    if (shown == 0) {
        Text none = rtl_block_make(cr, 13, 400, "אין אימג'ים זמינים לשחזור.", avail_w);
        text_draw_r(cr, &none, W - N_PAD, y, t->muted);
        text_free(&none);
    }
    int rows = (shown + cols - 1) / cols;
    if (rows < 1) rows = 1;
    y += rows * (image_h + grid_gap) + 3;

    double controls_y = H - N_STATUS_H - 94;
    if (y < controls_y) y = controls_y;
    char prompt[160];
    snprintf(prompt, sizeof prompt, "להמשך הקלידו את שם המחשב: %s",
             s->machine_name[0] ? s->machine_name : "לא הוגדר שם במרשם");
    Text label = rtl_block_make(cr, 12, 400, prompt, avail_w - 310);
    text_draw_r(cr, &label, W - N_PAD, y, s->machine_name[0] ? t->muted : t->danger);
    Rect input = {W - N_PAD - (avail_w - 310), y + label.h + 3, avail_w - 310, 38};
    draw_field(a, cr, input, a->restore_confirm, 0, NULL, HIT_RESTORE_CONFIRM);

    int exact = target && a->restore_image_sel > 0 && s->machine_name[0] &&
                strcmp(a->restore_confirm, s->machine_name) == 0;
    Text start = btn_label(cr, "מחק ושחזר");
    double sw = btn_width(&start);
    Rect start_box = {N_PAD, y + label.h + 3, sw, 38};
    if (exact) draw_btn(a, cr, start_box, &start, BTN_DANGER, HIT_RESTORE_START);
    else disabled_button(cr, t, start_box, &start);
    Text back = btn_label(cr, "חזרה");
    double bw = btn_width(&back);
    draw_btn(a, cr, (Rect){N_PAD + sw + 10, y + label.h + 3, bw, 38},
             &back, BTN_PLAIN, HIT_BACK);

    if (s->form_error[0]) {
        Text err = error_make(cr, s->form_error, avail_w);
        text_draw_r(cr, &err, W - N_PAD, input.y + input.h + 3, t->danger);
        text_free(&err);
    }

    text_free(&sh); text_free(&sl); text_free(&sv);
    text_free(&dh); text_free(&dl); text_free(&dv); text_free(&di);
    text_free(&warning); text_free(&gh); text_free(&label);
    text_free(&start); text_free(&back);
}
