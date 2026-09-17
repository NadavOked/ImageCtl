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

static double draw_header(App *a, cairo_t *cr, double W) {
    const Theme *t = a->theme;
    /* .native-brand: "ImageCtl" 700 ink, then <span> 500 in the accent blue
     * with margin-right 6 -- the span is this station's role (--title). */
    Text brand = text_make(cr, FONT_SANS, N_BRAND_FONT, 700, "ImageCtl", 0, DIR_LTR);
    Text role = text_make(cr, FONT_SANS, N_BRAND_FONT, 500, a->title, 0, DIR_RTL);
    double bx = W - N_HEADER_PAD;
    text_draw_r(cr, &brand, bx, (N_HEADER_H - brand.h) / 2, t->ink);
    bx -= brand.w + N_BRAND_GAP;
    text_draw_r(cr, &role, bx, (N_HEADER_H - role.h) / 2, t->brand_accent);
    text_free(&brand); text_free(&role);
    char identity[128];
    snprintf(identity, sizeof identity, "%s  %s", a->mac, a->ip);
    Text id = text_make(cr, FONT_MONO, N_HEADER_FONT, 400, identity, 0, DIR_LTR);
    text_draw(cr, &id, N_HEADER_PAD, (N_HEADER_H - id.h) / 2, t->muted);
    if (!a->force_cloner) {
        Rect b = { N_HEADER_PAD + id.w + N_ACTION_GAP,
                   (N_HEADER_H - N_CHOICE_ICON) / 2, N_CHOICE_ICON, N_CHOICE_ICON };
        draw_fill_rrect(cr, b, RADIUS_SM, t->button);
        draw_theme_glyph(cr, t->ink, t->button, b.x + b.w / 2, b.y + b.h / 2,
                         a->theme == &THEME_DARK);
        hit_add(a, b, HIT_THEME);
    }
    text_free(&id);
    cairo_rectangle(cr, 0, N_HEADER_H - N_BORDER, W, N_BORDER);
    draw_set(cr, t->hair); cairo_fill(cr);
    return N_HEADER_H;
}

/* The mockup's status word is the screen's own state ("ממתין לאימות",
 * "בחירת דיסק", "כותב 3 דיסקים במקביל"), not server health -- so it is
 * derived from what is already on the screen, nothing new from the agent. */
static const char *status_word(const App *a, char *buf, size_t n, Rgb *dot) {
    const State *s = &a->st;
    *dot = a->theme->led_ok;                                   /* .native-status i: --green */
    switch (a->screen) {
    case SCREEN_LOGIN:    return "ממתין לאימות";
    case SCREEN_MENU:     return "בחירת פעולה";
    case SCREEN_MESSAGE:  return s->msg_title;
    case SCREEN_PICK:     return "בחירת דיסק";
    case SCREEN_PROGRESS:
        if (s->task_disk[0]) {
            if (s->task_direct) snprintf(buf, n, "משדר מ־%s", s->task_disk);   /* #715 */
            else                snprintf(buf, n, "קולט מ־%s", s->task_disk);
            return buf;
        }
        return s->task == TASK_PENDING ? "ממתין לסוכן" : "משימה פעילה";
    case SCREEN_DONE:
        if (s->task == TASK_FAILED) { *dot = a->theme->danger; return "נכשל"; }
        return "הושלם";
    case SCREEN_CLASS:
        if (!s->has_session) return "בחירת כיתה";
        return s->sess_open ? "ממתין להצטרפות" : "משדר";
    case SCREEN_ROOM:
        if (s->has_round) {
            if (s->wave_open) snprintf(buf, n, "גל %d ממתין", s->wave_number);
            else              snprintf(buf, n, "גל %d משדר", s->wave_number);
            return buf;
        }
        else { int awake = 0; for (int i = 0; i < s->nmachines; i++) awake += s->machines[i].awake;
               snprintf(buf, n, "%d יעדים מחוברים", awake); return buf; }
    case SCREEN_CLONER:
        if (s->smart_pending) { *dot = a->theme->warn; return "ממתין להכרעת SMART"; }
        if (s->ndrawers) { int w = 0; for (int i = 0; i < s->ndrawers; i++) w += !strcmp(s->drawers[i].state, "writing");
                           snprintf(buf, n, "כותב %d דיסקים במקביל", w); return buf; }
        return "ממתין לסבב";
    case SCREEN_RESTORE:  return "בחירת אימג' לשחזור";
    }
    return "מוכן";
}

static void draw_status(App *a, cairo_t *cr, double W, double H) {
    const Theme *t = a->theme;
    Rect bar = {0, H - N_STATUS_H, W, N_STATUS_H};
    draw_fill_rrect(cr, bar, 0, t->status_bg);
    cairo_rectangle(cr, 0, bar.y, W, N_BORDER);                /* border-top */
    draw_set(cr, t->hair); cairo_fill(cr);
    Text label = text_make(cr, FONT_SANS, N_HEADER_FONT, 400, "ImageCtl · תחנת קצה", 0, DIR_RTL);
    text_draw_r(cr, &label, W - N_STATUS_PAD, bar.y + (bar.h - label.h) / 2, t->muted);
    text_free(&label);
    char buf[96]; Rgb dot;
    const char *word = status_word(a, buf, sizeof buf, &dot);
    draw_status_word(cr, t, N_STATUS_PAD, bar.y + bar.h / 2, word, dot);
}

/* ---- #st-login ------------------------------------------------------------- */

void screen_login(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    double cw = fmin(N_NARROW_W, card_width(W)), inner = cw - 2 * N_PAD;
    Text title = rtl_block_make(cr, N_TITLE, 500, "התחברות ל־ImageCtl", inner);
    Text sub = rtl_block_make(cr, N_SUB, 400, "אימות נדרש לפני גישה למסך התחנה.", inner);
    Text user = label_make(cr, "שם משתמש", inner), pass = label_make(cr, "סיסמה", inner);
    Text err = error_make(cr, a->error, inner), button = btn_label(cr, "התחבר");
    Text eye = btn_label(cr, a->show_pw ? "הסתר" : "הצג");
    double ch = 2 * N_PAD + N_LOGO + N_LOGO_GAP + title.h + N_TITLE_GAP + sub.h
              + N_FORM_TOP + user.h + pass.h + 2 * N_LABEL_GAP + 2 * N_FIELD_H
              + N_FORM_GAP + N_ACTION_TOP + N_BUTTON_H + (a->error[0] ? err.h + N_FORM_GAP : 0);
    Rect panel = {(W-cw)/2, head_h + (H-head_h-N_STATUS_H-ch)/2, cw, ch};
    draw_box_shadow(cr, panel, RADIUS_R, N_SHADOW_Y, N_SHADOW_BLUR, 0, t->shadow_strong, t->shadow_strong_a);
    draw_fill_rrect(cr, panel, RADIUS_R, t->surface);
    draw_border_rrect(cr, panel, RADIUS_R, t->hair, N_BORDER);
    double x = panel.x + N_PAD, y = panel.y + N_PAD;
    draw_native_icon(cr, t, (Rect){x+inner-N_LOGO,y,N_LOGO,N_LOGO}, ICON_DISK);
    y += N_LOGO + N_LOGO_GAP;
    text_draw(cr, &title, x, y, t->ink); y += title.h + N_TITLE_GAP;
    text_draw(cr, &sub, x, y, t->muted); y += sub.h + N_FORM_TOP;
    text_draw(cr, &user, x, y, t->muted); y += user.h + N_LABEL_GAP;
    draw_field(a, cr, (Rect){x,y,inner,N_FIELD_H}, a->user, 0, NULL, HIT_USER);
    y += N_FIELD_H + N_FORM_GAP;
    text_draw(cr, &pass, x, y, t->muted); y += pass.h + N_LABEL_GAP;
    draw_field(a, cr, (Rect){x,y,inner,N_FIELD_H}, a->pass, !a->show_pw, "סיסמה", HIT_PASS);   /* placeholder="סיסמה" */
    double ew = eye.w + 2 * N_ACTION_GAP;
    draw_btn(a, cr, (Rect){x+inner-ew-N_BORDER,y+N_BORDER,ew,N_FIELD_H-2*N_BORDER}, &eye, BTN_PLAIN, HIT_EYE);
    y += N_FIELD_H;
    if (a->error[0]) { y += N_FORM_GAP; text_draw(cr, &err, x, y, t->danger); y += err.h; }
    y += N_ACTION_TOP;
    double bw = btn_width(&button);
    draw_btn(a, cr, (Rect){x+inner-bw,y,bw,N_BUTTON_H}, &button, BTN_PRIMARY, HIT_SUBMIT);
    text_free(&title); text_free(&sub); text_free(&user); text_free(&pass);
    text_free(&err); text_free(&button); text_free(&eye);
}

/* ---- #st-menu -------------------------------------------------------------- */

typedef struct { const char *b, *s; int id, multi; } MenuItem;

/* Order: the two cards that touch THIS machine's disk first (read it, write
 * it -- single tray), then the two that broadcast (multi tray). The restore
 * card is #382 and is not in index.html yet; its text is the issue's. */
#define MENU_N 5
static const MenuItem MENU[MENU_N] = {
    { "קליטת אימג' חדש",            "קוראים את הכונן שבמכונה ומעלים לספרייה",                       HIT_CAPTURE, 0 },
    { "משיכת אימג' לכונן המחשב הזה", "כתוב אימג' מהספרייה על הדיסק של המחשב הזה — כמו שכפול בודד",   HIT_RESTORE, 0 },
    { "הפצה למחשבי שיכפול",        "משדרים אימג' לכל המגירות בחדר, בגלים, עד היעד",                  HIT_ROOM,    1 },
    /* #715: between the room and the classes, as the issue orders it. */
    { "הפצה מהדיסק הזה למחשבי השיכפול", "הדיסק של המחשב הזה משודר ישירות למגירות שנבחרו — בלי לשמור אימג' בשרת", HIT_DIRECT, 1 },
    { "הפצה לכיתות",               "בוחרים כיתה, מחשבים ואימג' — הסבב מעיר את הכיתה ורץ בשרת",       HIT_CLASSES, 1 },
};

void screen_menu(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme;
    double inner = card_width(W) - 2 * N_PAD;
    char sub[128]; snprintf(sub, sizeof sub, "מחוברים כ־%s", a->signed_user);
    Head hd = head_make(cr, "מה תרצה להפעיל?", sub, inner);
    head_logo(&hd);                                        /* nativeMenu: .native-logo first */
    /* Only capture is admin-only (station.js); restore is shown to the
     * deploy role like room/classes -- #382 leaves that permission to Nadav,
     * and this is the one place to flip it. The direct card (#715) is always
     * shown, like room; the class card follows the server's switch (#880,
     * menu_class= in the state file; absent = off). */
    int show[MENU_N] = {a->admin, 1, 1, 1, a->st.menu_class}, count = 0;
    for (int i = 0; i < MENU_N; i++) count += show[i] ? 1 : 0;
    /* The mockup's grid is two columns. Five cards (#715, admin with the
     * class switch on) would be three rows of min-height 160, which is
     * ~698px with the head -- past the 696 a 1280x800 screen leaves --
     * so five go three across, two rows, and nothing is clipped. */
    int cols = inner < N_NARROW_W ? 1 : count > 4 ? 3 : 2;
    int rows = (count + cols - 1) / cols;
    double cw = (inner - (cols - 1) * N_CHOICE_GAP) / cols;
    double tw = cw - 2 * N_CHOICE_PAD;
    Text title[MENU_N], desc[MENU_N];
    double ch = N_CHOICE_H;
    for (int i = 0; i < MENU_N; i++) if (show[i]) {
        title[i] = rtl_block_make(cr, N_CHOICE_TITLE, 700, MENU[i].b, tw);
        desc[i] = rtl_block_make(cr, N_CHOICE_SUB, 400, MENU[i].s, tw);
        ch = dmax(ch, 2*N_CHOICE_PAD + N_CHOICE_ICON + N_ACTION_GAP + title[i].h + desc[i].h);
    }
    Rect body, foot;
    card_frame(a, cr, W, H, head_h, &hd,
               N_PAD + rows * ch + (rows - 1)*N_CHOICE_GAP, 0, &body, &foot);
    body_clip_begin(a, cr, body);
    int visible = 0;
    for (int i = 0; i < MENU_N; i++) if (show[i]) {
        Rect c = {body.x + N_PAD + inner - cw - (visible % cols)*(cw+N_CHOICE_GAP),
                  body.y + N_PAD + (visible / cols)*(ch+N_CHOICE_GAP), cw, ch};
        int selected = hovered(a,c) || a->menu_focus == visible;
        draw_fill_rrect(cr,c,RADIUS_R,selected ? t->indigo_soft : t->choice);
        draw_border_rrect(cr,c,RADIUS_R,selected ? t->selected_line : t->choice_line,N_BORDER);
        double x = c.x + N_CHOICE_PAD, y = c.y + N_CHOICE_PAD;
        draw_native_icon(cr,t,(Rect){x+tw-N_CHOICE_ICON,y,N_CHOICE_ICON,N_CHOICE_ICON},
                         MENU[i].multi ? ICON_NETWORK : ICON_DISK);
        y += N_CHOICE_ICON + N_ACTION_GAP;
        text_draw(cr,&title[i],x,y,t->ink); y += title[i].h;
        text_draw(cr,&desc[i],x,y,t->muted);
        hit_add(a,c,MENU[i].id);
        text_free(&title[i]); text_free(&desc[i]); visible++;
    }
    body_clip_end(a,cr); card_end(a,cr);
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
    /* A cloning machine has no login, menu or task screen -- its own screen
     * wins before any of that routing (before even a message). */
    if (a->force_cloner) { a->screen = SCREEN_CLONER; return; }
    if (s->msg_title[0]) { a->screen = SCREEN_MESSAGE; return; }        /* !state.known etc. */
    if (a->force_progress || s->task == TASK_PENDING || s->task == TASK_RUNNING) {
        a->screen = SCREEN_PROGRESS; a->watching = 1;                     /* a running task always wins */
        return;
    }
    if (a->watching && (s->task == TASK_DONE || s->task == TASK_FAILED)) {
        /* it finished while we were watching -> drawDone */
        a->watching = 0; a->showing_done = 1;
        if (s->task == TASK_DONE && s->task_direct) {           /* #715 */
            snprintf(a->done_title, sizeof a->done_title, "ההפצה הישירה הושלמה");
            snprintf(a->done_sub, sizeof a->done_sub,
                     "הדיסק %s שודר למגירות שנבחרו. שום דבר לא נשמר בשרת.", s->task_disk);
        } else if (s->task == TASK_DONE) {
            snprintf(a->done_title, sizeof a->done_title, "הקליטה הושלמה");
            snprintf(a->done_sub, sizeof a->done_sub,
                     "\"%s\" נכנס לספרייה. אפשר לכבות ולשלוף את הכונן.", s->task_name);
        } else {
            snprintf(a->done_title, sizeof a->done_title,
                     s->task_direct ? "ההפצה הישירה נכשלה" : "הקליטה נכשלה");
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
    case MODE_DIRECT:  a->screen = SCREEN_ROOM; break;   /* #715: same screen, no image picker */
    case MODE_CAPTURE: a->screen = SCREEN_PICK; break;
    case MODE_RESTORE: a->screen = SCREEN_RESTORE; break;
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
    case SCREEN_CLONER:   screen_cloner(a, cr, W, H, head_h); break;
    case SCREEN_MESSAGE:  screen_message(a, cr, W, H, head_h); break;
    case SCREEN_RESTORE:  screen_restore(a, cr, W, H, head_h); break;
    }
    draw_status(a, cr, W, H);
    draw_toast(a, cr, W, H);
    /* The cursor is NOT drawn here: the render loop caches this scene and
     * overlays the pointer separately, so a pointer-only move need not re-run
     * this whole (Pango-heavy) draw. See app_draw_pointer + main.c. */
}

/* Overlay the software pointer on top of an already-composed scene. */
void app_draw_pointer(App *a, cairo_t *cr) {
    if (a->ptr_visible) draw_cursor(cr, a->ptr_x, a->ptr_y);
}

int app_hit(const App *a, double x, double y) {
    for (int i = a->nhits - 1; i >= 0; i--)          /* last drawn = topmost */
        if (rect_has(a->hits[i].r, x, y)) return a->hits[i].id;
    return HIT_NONE;
}
