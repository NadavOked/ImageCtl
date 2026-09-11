/* The header, #st-login and #st-menu, the routing rule of station.js
 * poll(), and the draw entry point. Every constant here is a value in
 * server/static/console.css or station.css, cited inline; the text is the
 * text of server/static/station/index.html. This is a re-skin in another
 * renderer, not a redesign. The other cards: screens_capture.c
 * (#st-pick, #st-progress, #st-done, #st-message) and screens_rounds.c
 * (#st-room, #st-class). */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "widgets.h"

/* console.css .mark (+ station.css: 40x40, radius 11):
 * ::before top 11 left 8 right 8 height 3 radius 2 mark-line
 * ::after  top 19 right 8 width 9 height 3 led-write */
static void draw_mark(cairo_t *cr, const Theme *t, double x, double y) {
    draw_fill_rrect(cr, (Rect){ x, y, 40, 40 }, 11, t->ink);
    draw_fill_rrect(cr, (Rect){ x + 8, y + 11, 40 - 16, 3 }, 2, t->mark_line);
    draw_fill_rrect(cr, (Rect){ x + 40 - 8 - 9, y + 19, 9, 3 }, 2, t->led_write);
}

/* station.js sets the button text to "☾" (light) / "☀" (dark). Those code
 * points are not in IBM Plex, and the initramfs carries no other font, so a
 * glyph would be a tofu box. Drawn as vectors instead -- same meaning. */
static void draw_theme_glyph(cairo_t *cr, Rgb ink, Rgb bg, double cx, double cy, int dark) {
    if (dark) {                                 /* ☀ */
        cairo_arc(cr, cx, cy, 3.2, 0, 2 * M_PI);
        draw_set(cr, ink); cairo_fill(cr);
        cairo_set_line_width(cr, 1.4);
        for (int k = 0; k < 8; k++) {
            double an = k * M_PI / 4;
            cairo_move_to(cr, cx + 5.0 * cos(an), cy + 5.0 * sin(an));
            cairo_line_to(cr, cx + 7.2 * cos(an), cy + 7.2 * sin(an));
        }
        cairo_stroke(cr);
    } else {                                    /* ☾ */
        cairo_arc(cr, cx, cy, 6, 0, 2 * M_PI);
        draw_set(cr, ink); cairo_fill(cr);
        cairo_arc(cr, cx - 3.0, cy - 1.2, 5.4, 0, 2 * M_PI);
        draw_set(cr, bg); cairo_fill(cr);
    }
}

/* ---- header (station.css .st-head) ----------------------------------------- */

#define HEAD_PX 34.0     /* padding: 26px 34px */
#define HEAD_PY 26.0

static double draw_header(App *a, cairo_t *cr, double W) {
    const Theme *t = a->theme;

    /* .st-brand, at the right: mark, gap 12, then h1 over p (centre-aligned
     * against the 40px mark). */
    double mx = W - HEAD_PX - 40, my = HEAD_PY;
    draw_mark(cr, t, mx, my);
    Text h1 = text_make(cr, FONT_SANS, 18, 600, a->title, 0, DIR_RTL);
    Text p  = text_make(cr, FONT_SANS, 12, 400, "שרת אימג'ים — מכללה", 0, DIR_RTL);
    double bh = h1.h + 2 + p.h, by = my + (40 - bh) / 2;
    text_draw_r(cr, &h1, mx - 12, by, HEAD_TITLE);
    text_draw_r(cr, &p,  mx - 12, by + h1.h + 2, HEAD_SUB);
    text_free(&h1); text_free(&p);

    /* .st-tools at the left. In an RTL flex row the first child (#st-theme)
     * is the rightmost of the group, so the id block is at the far left and
     * the button sits 14px to its right. .st-id: mono 13px, line-height 1.7,
     * text-align:left, dir=ltr. */
    double lh = 13 * 1.7;
    Text mac = text_make(cr, FONT_MONO, 13, 400, a->mac[0] ? a->mac : "–", 0, DIR_LTR);
    Text ip  = text_make(cr, FONT_MONO, 13, 400, a->ip, 0, DIR_LTR);
    double idw = dmax(mac.w, a->ip[0] ? ip.w : 0);
    text_draw(cr, &mac, HEAD_PX, my + (lh - mac.h) / 2, HEAD_SUB);
    if (a->ip[0]) text_draw(cr, &ip, HEAD_PX, my + lh + (lh - ip.h) / 2, HEAD_SUB);
    text_free(&mac); text_free(&ip);

    /* .btn.icon: padding 7px 11px, font 15px, line-height 1 -> 39x31 */
    Rect b = { HEAD_PX + idw + 14, my, 11 + 15 + 11 + 2, 7 + 15 + 7 + 2 };
    int hov = hovered(a, b);
    Rgb bg = hov ? t->btn_hover : t->surface;
    draw_fill_rrect(cr, b, RADIUS_SM, bg);
    draw_border_rrect(cr, b, RADIUS_SM, hov ? t->btn_hover_line : t->hair, 1);
    draw_theme_glyph(cr, t->ink, bg, b.x + b.w / 2, b.y + b.h / 2, a->theme == &THEME_DARK);
    hit_add(a, b, HIT_THEME);

    double tools_h = dmax(2 * lh, b.h);
    return HEAD_PY + dmax(40, tools_h) + HEAD_PY;
}

/* ---- #st-login ------------------------------------------------------------- */

void screen_login(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    double inner = card_width(W) - 44;                          /* .sbody padding 22 */

    Head hd = head_make(cr, "כניסה", "הפעולות במסך הזה דורשות חשבון קונסולה.", inner);
    Text l1  = label_make(cr, "שם משתמש", inner);
    Text l2  = label_make(cr, "סיסמה", inner);
    Text err = error_make(cr, a->error, inner);
    Text bt  = btn_label(cr, "כניסה");
    Text eye = text_make(cr, FONT_SANS, 11.5, 400, a->show_pw ? "הסתר" : "הצג", 0, DIR_RTL);

    double in_h  = field_height(cr);
    double err_h = dmax(err.h, ERROR_MIN_H);
    /* Vertical rhythm, from the CSS margins (station.css: label margin-top 6;
     * console.css: label margin-bottom 6, input margin-bottom 14, .error
     * margin-top 6; sibling margins collapse, parent/child ones do not). */
    double body_h = 22 + 6 + l1.h + 2 + in_h + 20 + l2.h + 6 + in_h + 20 + err_h + 22;
    double btn_h  = btn_height(cr);
    double foot_h = foot_height(cr, btn_h);

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, foot_h, &body, &foot);
    body_clip_begin(a, cr, body);
    double x = body.x + 22, y = body.y + 22 + 6;
    text_draw(cr, &l1, x, y, t->muted);              y += l1.h + 2;
    draw_field(a, cr, (Rect){ x, y, inner, in_h }, a->user, 0, NULL, HIT_USER);
    y += in_h + 20;
    text_draw(cr, &l2, x, y, t->muted);              y += l2.h + 6;
    Rect f2 = { x, y, inner, in_h };
    draw_field(a, cr, f2, a->pass, !a->show_pw, NULL, HIT_PASS);
    /* .pw-eye: inset-inline-end 8 (left), top 9, padding 4px 9px, radius 6 */
    Rect e = { f2.x + 8, f2.y + 9, 9 + eye.w + 9 + 2, 4 + eye.h + 4 + 2 };
    {
        int hov = hovered(a, e);
        draw_fill_rrect(cr, e, 6, t->surface);
        draw_border_rrect(cr, e, 6, hov ? t->btn_hover_line : t->hair, 1);
        text_draw(cr, &eye, e.x + (e.w - eye.w) / 2, e.y + (e.h - eye.h) / 2,
                  hov ? t->ink : t->muted);
        hit_add(a, e, HIT_EYE);
    }
    y += in_h + 20;
    text_draw(cr, &err, x, y, t->danger);
    body_clip_end(a, cr);

    /* the primary button is the first flex child -> at the right. */
    foot_draw_bg(cr, t, foot);
    double bw = btn_width(&bt);
    draw_btn(a, cr, (Rect){ foot.x + foot.w - 22 - bw, foot.y + 1 + 14, bw, btn_h }, &bt, BTN_PRIMARY, HIT_SUBMIT);
    card_end(a, cr);

    text_free(&l1); text_free(&l2); text_free(&err); text_free(&bt); text_free(&eye);
}

/* ---- #st-menu -------------------------------------------------------------- */

typedef struct { const char *b, *s; int id, multi; } MenuItem;

/* Order: the two cards that touch THIS machine's disk first (read it, write
 * it -- single tray), then the two that broadcast (multi tray). The restore
 * card is #382 and is not in index.html yet; its text is the issue's. */
#define MENU_N 4
static const MenuItem MENU[MENU_N] = {
    { "קליטת אימג' חדש",            "קוראים את הכונן שבמכונה ומעלים לספרייה",                       HIT_CAPTURE, 0 },
    { "משיכת אימג' לכונן המחשב הזה", "כתוב אימג' מהספרייה על הדיסק של המחשב הזה — כמו שכפול בודד",   HIT_RESTORE, 0 },
    { "הפצה למחשבי שיכפול",        "משדרים אימג' לכל המגירות בחדר, בגלים, עד היעד",                  HIT_ROOM,    1 },
    { "הפצה לכיתות",               "בוחרים כיתה, מחשבים ואימג' — הסבב מעיר את הכיתה ורץ בשרת",       HIT_CLASSES, 1 },
};

void screen_menu(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    double inner = card_width(W) - 44;
    char sub[128];
    snprintf(sub, sizeof sub, "מחוברים כ־%s", a->signed_user);      /* station.js afterLogin */
    Head hd = head_make(cr, "מה עושים?", sub, inner);

    /* .menu-card: padding 18px 16px, gap 14, tray 52 wide; <b> is 700 */
    double text_w = inner - 16 - 52 - 14 - 16;
    Text bt[MENU_N], st[MENU_N];
    double card_h[MENU_N], body_h = 22 + 22;
    /* Only capture is admin-only (station.js); restore is shown to the
     * deploy role like room/classes -- #382 leaves that permission to Nadav,
     * and this is the one place to flip it. */
    int show[MENU_N] = { a->admin, 1, 1, 1 };
    for (int i = 0; i < MENU_N; i++) {
        if (!show[i]) continue;
        bt[i] = text_make(cr, FONT_SANS, 15, 700, MENU[i].b, (int)text_w, DIR_RTL);
        st[i] = text_make(cr, FONT_SANS, 12.5, 400, MENU[i].s, (int)text_w, DIR_RTL);
        card_h[i] = 18 + dmax(34, bt[i].h + st[i].h) + 18 + 2;
        body_h += card_h[i] + 10;                                   /* margin-bottom 10 */
    }

    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd, body_h, 0, &body, &foot);
    body_clip_begin(a, cr, body);
    double y = body.y + 22;
    int visible_index = 0;
    for (int i = 0; i < MENU_N; i++) {
        if (!show[i]) continue;
        Rect c = { body.x + 22, y, inner, card_h[i] };
        int hov = hovered(a, c) || a->menu_focus == visible_index;
        draw_fill_rrect(cr, c, 12, hov ? t->indigo_soft : t->field);
        draw_border_rrect(cr, c, 12, hov ? t->indigo : t->hair, 1);
        double tx = c.x + c.w - 16 - 52, ty = c.y + (c.h - 34) / 2;
        draw_tray(cr, t, tx, ty, MENU[i].multi);
        double block = bt[i].h + st[i].h, by = c.y + (c.h - block) / 2;
        text_draw(cr, &bt[i], tx - 14 - text_w, by, t->ink);
        text_draw(cr, &st[i], tx - 14 - text_w, by + bt[i].h, t->muted);
        hit_add(a, c, MENU[i].id);
        text_free(&bt[i]); text_free(&st[i]);
        y += card_h[i] + 10;
        visible_index++;
    }
    body_clip_end(a, cr);
    card_end(a, cr);
}

/* ---- pointer ----------------------------------------------------------------- */

static void draw_cursor(cairo_t *cr, double x, double y) {
    static const double P[][2] = { {0,0}, {0,16}, {4,12}, {7,19}, {9.5,18}, {6.5,11}, {11.5,11} };
    cairo_move_to(cr, x + P[0][0], y + P[0][1]);
    for (int i = 1; i < 7; i++) cairo_line_to(cr, x + P[i][0], y + P[i][1]);
    cairo_close_path(cr);
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_fill_preserve(cr);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_set_line_width(cr, 1);
    cairo_stroke(cr);
}

/* ---- routing: station.js poll(), in its order ------------------------------- */

void app_route(App *a) {
    const State *s = &a->st;
    if (s->msg_title[0]) { a->screen = SCREEN_MESSAGE; return; }        /* !state.known etc. */
    if (a->force_progress || s->task == TASK_PENDING || s->task == TASK_RUNNING) {
        a->screen = SCREEN_PROGRESS; a->watching = 1;                     /* a running task always wins */
        return;
    }
    if (a->watching && (s->task == TASK_DONE || s->task == TASK_FAILED)) {
        /* it finished while we were watching -> drawDone */
        a->watching = 0; a->showing_done = 1;
        if (s->task == TASK_DONE) {
            snprintf(a->done_title, sizeof a->done_title, "הקליטה הושלמה");
            snprintf(a->done_sub, sizeof a->done_sub,
                     "\"%s\" נכנס לספרייה. אפשר לכבות ולשלוף את הכונן.", s->task_name);
        } else {
            snprintf(a->done_title, sizeof a->done_title, "הקליטה נכשלה");
            snprintf(a->done_sub, sizeof a->done_sub, "%s",
                     s->task_error[0] ? s->task_error : "ראו את היומן בקונסולה.");
        }
    }
    if (a->showing_done) { a->screen = SCREEN_DONE; return; }            /* until "קליטה נוספת" */
    /* A live classroom round stays visible without a session (#34): the
     * screen stands in the classroom, and the view needs no login. */
    if (a->mode == MODE_CLASSES && (a->signed_in || s->has_session)) { a->screen = SCREEN_CLASS; return; }
    if (!a->signed_in) { a->screen = SCREEN_LOGIN; return; }
    switch (a->mode) {
    case MODE_ROOM:    a->screen = SCREEN_ROOM; break;
    case MODE_CAPTURE: a->screen = SCREEN_PICK; break;
    default:           a->screen = SCREEN_MENU; break;
    }
}

/* ---- entry points ------------------------------------------------------------ */

void app_draw(App *a, cairo_t *cr, int W, int H) {
    a->nhits = 0; a->clip_on = 0;
    app_route(a);
    draw_station_background(cr, a->theme, W, H);
    double head_h = draw_header(a, cr, W);
    switch (a->screen) {
    case SCREEN_LOGIN:    screen_login(a, cr, W, H, head_h); break;
    case SCREEN_MENU:     screen_menu(a, cr, W, H, head_h); break;
    case SCREEN_PICK:     screen_pick(a, cr, W, H, head_h); break;
    case SCREEN_PROGRESS: screen_progress(a, cr, W, H, head_h); break;
    case SCREEN_DONE:     screen_done(a, cr, W, H, head_h); break;
    case SCREEN_ROOM:     screen_room(a, cr, W, H, head_h); break;
    case SCREEN_CLASS:    screen_class(a, cr, W, H, head_h); break;
    case SCREEN_MESSAGE:  screen_message(a, cr, W, H, head_h); break;
    }
    draw_toast(a, cr, W, H);
    if (a->ptr_visible) draw_cursor(cr, a->ptr_x, a->ptr_y);
}

int app_hit(const App *a, double x, double y) {
    for (int i = a->nhits - 1; i >= 0; i--)          /* last drawn = topmost */
        if (rect_has(a->hits[i].r, x, y)) return a->hits[i].id;
    return HIT_NONE;
}
