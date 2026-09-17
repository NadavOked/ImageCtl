#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"

/* ---- #st-class --------------------------------------------------------------- */

void screen_class(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    const State *s = &a->st;
    double inner = card_width(W) - 2 * N_PAD, in_h = field_height(cr), btn_h = btn_height(cr);

    if (!s->has_session) {
        /* ---- renderClassPick (step 1 of the wizard) ---- */
        Head hd = head_make(cr, "הפצה לכיתות", "לאיזו כיתה נפתח הסבב", inner);
        double text_w = inner - 2 * N_METRIC_PAD;
        Text cb[MAX_CLASSES], cs[MAX_CLASSES];
        double ch[MAX_CLASSES], body_h = N_PAD + RADIUS_SM + N_ACTION_TOP;
        Text none = { NULL, 0, 0 };
        if (s->nclasses == 0) {
            none = sub_make(cr, "אין עדיין כיתות — מגדירים אותן בקונסולה, במסך המחשבים.", inner);
            body_h += none.h;
        }
        for (int i = 0; i < s->nclasses; i++) {
            char small[128];
            if (s->classes[i].machines) snprintf(small, sizeof small, "%d מחשבים רשומים", s->classes[i].machines);
            else snprintf(small, sizeof small, "אין מחשבים רשומים — מוסיפים בקונסולה, במסך המחשבים");
            cb[i] = text_make(cr, FONT_SANS, N_CHOICE_TITLE, 700, s->classes[i].label, (int)text_w, DIR_RTL);
            cs[i] = text_make(cr, FONT_SANS, N_SUB, 400, small, (int)text_w, DIR_RTL);
            ch[i] = 2 * N_METRIC_PAD + cb[i].h + cs[i].h;
            body_h += ch[i] + N_ACTION_GAP;
        }
        body_h += N_PAD;
        Text labels[1] = { btn_label(cr, "חזרה") };
        static const int kinds[1] = { BTN_PLAIN }, ids[1] = { HIT_BACK };

        Rect body, foot;
        card_frame(a, cr, W, H, head_h, &hd, body_h, foot_height(cr, btn_h), &body, &foot);
        body_clip_begin(a, cr, body);
        double x = body.x + N_PAD, y = body.y + N_PAD;
        draw_cls_bars(cr, t, x, y, inner, 1);            y += RADIUS_SM + N_ACTION_TOP;
        if (s->nclasses == 0) { text_draw(cr, &none, x, y, t->ink); text_free(&none); }
        for (int i = 0; i < s->nclasses; i++) {
            Rect c = { x, y, inner, ch[i] };
            int dim = s->classes[i].machines == 0;
            int hov = !dim && hovered(a, c);              /* .menu-card.dim:hover stays plain */
            if (dim) cairo_push_group(cr);                /* opacity:.55 */
            draw_fill_rrect(cr, c, RADIUS_R, hov ? t->indigo_soft : t->choice);
            draw_border_rrect(cr, c, RADIUS_R, hov ? t->selected_line : t->round_line, N_BORDER);
            double tx = c.x + c.w - N_METRIC_PAD;
            double block = cb[i].h + cs[i].h, by = c.y + (c.h - block) / 2;
            text_draw(cr, &cb[i], tx - text_w, by, t->ink);
            text_draw(cr, &cs[i], tx - text_w, by + cb[i].h, t->muted);
            if (dim) { cairo_pop_group_to_source(cr); cairo_paint_with_alpha(cr, N_DIM_ALPHA); }
            hit_add(a, c, HIT_CLASS_BASE + i);
            text_free(&cb[i]); text_free(&cs[i]);
            y += ch[i] + N_ACTION_GAP;
        }
        body_clip_end(a, cr);
        foot_draw_bg(cr, t, foot);
        foot_buttons(a, cr, foot, labels, kinds, ids, 1);
        card_end(a, cr);
        return;
    }

    /* ---- renderLive: nativeClass ----
     * .native-class-grid 1.2fr/.8fr: the round card (.native-round-id 20px,
     * .native-round-meta 10px, a .native-alert with the hint, the "מחשבים
     * בסבב" line with the count at the inline end, .native-bar 5px, and the
     * actions inside the card) beside a column of .native-metric boxes. The
     * mockup's PXE / Multicast metrics have no field in the state file. The
     * machine rows and the stop-confirm box below are the existing live view. */
    char sub[256], joined[48], hint[300], rid[160], meta[160];   /* sub: 96 + 32 + 80 + separators */
    snprintf(sub, sizeof sub, "משדר: %s · %s — %s", s->sess_image, s->sess_prefix, s->sess_group);
    snprintf(joined, sizeof joined, "%d / %d", s->joined, s->expected);
    snprintf(rid, sizeof rid, "%s · סבב פעיל", s->sess_group);
    snprintf(meta, sizeof meta, "%s · %s", s->sess_image, s->sess_prefix);
    if (s->sess_open)
        snprintf(hint, sizeof hint, "הסבב פתוח — כל מחשב שעולה מצטרף. השידור יתחיל כשכולם יגיעו, בעוד %d:%02d דקות, או בלחיצה.",
                 s->starts_in / 60, s->starts_in % 60);
    else
        snprintf(hint, sizeof hint, "השידור רץ. עומדים בכיתה ורואים מי תקוע — בלי לעבור בין מסכים.");

    Head hd = head_make(cr, "הפצה לכיתות", sub, inner);
    double side_w = (inner-N_CHOICE_GAP) * N_CLASS_SECONDARY / (N_CLASS_PRIMARY+N_CLASS_SECONDARY);
    double main_w = inner-N_CHOICE_GAP-side_w, rw = main_w-2*N_ROUND_PAD;
    Text rid_t = rtl_block_make(cr, N_ROUND_ID, 400, rid, rw);
    Text meta_t = rtl_block_make(cr, N_ROUND_META, 400, meta, rw);
    Text hint_t = alert_make(cr, hint, rw);
    Text cnt_l = text_make(cr, FONT_SANS, N_METRIC_LABEL, 400, "מחשבים בסבב", 0, DIR_RTL);
    Text cnt_v = text_make(cr, FONT_SANS, N_METRIC_LABEL, 700, joined, 0, DIR_LTR);
    Text labels[3]; int kinds[3], ids[3], nb = 0;
    if (s->sess_open) { labels[nb] = btn_label(cr, "התחל עכשיו"); kinds[nb] = BTN_PRIMARY; ids[nb++] = HIT_CLASS_START; }
    labels[nb] = btn_label(cr, "עצור סבב"); kinds[nb] = BTN_DANGER; ids[nb++] = HIT_CLASS_CLOSE;
    labels[nb] = btn_label(cr, "חזרה");     kinds[nb] = BTN_PLAIN;  ids[nb++] = HIT_BACK;
    double cnt_h = dmax(cnt_l.h, cnt_v.h);
    double card_h = 2*N_ROUND_PAD + rid_t.h + meta_t.h + N_CHOICE_GAP + alert_height(&hint_t)
                  + N_LOGO_GAP + cnt_h + N_LABEL_GAP + N_BAR_SM + N_ACTION_TOP + btn_h;
    double mh=metric_height(cr), top_h=dmax(3*mh+2*N_METRIC_GAP, card_h);
    RowSet rs; rows_make(a, cr, inner, ROWS_CLASS, "עוד לא הצטרף אף מחשב — הכיתה מתעוררת.", &rs);
    Text cl = { NULL, 0, 0 };
    double confirm_h = 0;
    if (a->class_confirming) {
        cl = confirm_label(cr, s->sess_image, 0, inner);
        confirm_h = N_LABEL_GAP + cl.h + 2 + in_h + N_FORM_GAP;
    }
    Text err = error_make(cr, s->class_error, inner);
    double err_h = dmax(err.h, ERROR_MIN_H);
    double body_h = N_PAD + top_h + N_FORM_GAP + rs.h + confirm_h + N_LABEL_GAP + err_h;

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, 0, &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + N_PAD, y = body.y + N_PAD;
    Rect round={x+side_w+N_CHOICE_GAP,y,main_w,top_h};
    draw_fill_rrect(cr,round,0,t->choice);                     /* .native-round-card: no radius */
    draw_border_rrect(cr,round,0,t->round_line,N_BORDER);
    double rx=round.x+N_ROUND_PAD, ry=round.y+N_ROUND_PAD;
    text_draw(cr,&rid_t,rx,ry,t->ink);                          ry+=rid_t.h;
    text_draw(cr,&meta_t,rx,ry,t->muted);                       ry+=meta_t.h+N_CHOICE_GAP;
    draw_alert(cr,t,(Rect){rx,ry,rw,alert_height(&hint_t)},&hint_t); ry+=alert_height(&hint_t)+N_LOGO_GAP;
    text_draw_r(cr,&cnt_l,rx+rw,ry+(cnt_h-cnt_l.h)/2,t->muted);   /* strong{float:left} */
    text_draw(cr,&cnt_v,rx,ry+(cnt_h-cnt_v.h)/2,t->ink);         ry+=cnt_h+N_LABEL_GAP;
    int pct=s->expected>0 ? (int)(100.0*s->joined/s->expected) : 0;
    draw_thin_bar(cr,t,(Rect){rx,ry,rw,N_BAR_SM},pct,t->led_write); ry+=N_BAR_SM;
    foot_buttons(a, cr, (Rect){round.x, ry, main_w - N_ROUND_PAD + N_PAD, btn_h}, labels, kinds, ids, nb);
    draw_metric(cr,t,(Rect){x,y,side_w,mh},"כיתה",s->sess_group);
    draw_metric(cr,t,(Rect){x,y+mh+N_METRIC_GAP,side_w,mh},"מחשבים בסבב",joined);
    draw_metric(cr,t,(Rect){x,y+2*(mh+N_METRIC_GAP),side_w,mh},"אימג'",s->sess_image);
    y+=top_h+N_FORM_GAP;
    rows_draw(a, cr, x, y, inner, &rs);                  y += rs.h;
    if (a->class_confirming) {
        y += N_LABEL_GAP;
        text_draw(cr, &cl, x, y, t->muted);              y += cl.h + 2;
        draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->class_confirm, 0, NULL, HIT_CLASS_CONFIRM);
        y += in_h + N_FORM_GAP;
        text_free(&cl);
    }
    y += N_LABEL_GAP;
    text_draw(cr, &err, x, y, t->danger);
    body_clip_end(a, cr);
    card_end(a, cr);
    text_free(&rid_t); text_free(&meta_t); text_free(&hint_t); text_free(&cnt_l); text_free(&cnt_v); text_free(&err);
}
