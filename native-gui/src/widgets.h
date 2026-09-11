/* The pieces every card is built from -- console.css .btn / input /
 * select / .sheet / .error, station.css .tray / .big-bar / .cls-bars,
 * #toast -- drawn once here, used by the screens_*.c files. Every number
 * is a CSS value, cited where it is used. */
#ifndef IMAGECTL_WIDGETS_H
#define IMAGECTL_WIDGETS_H

#include "ui.h"
#include "text.h"

void hit_add(App *a, Rect r, int id);
int hovered(const App *a, Rect r);
double dmax(double x, double y);

/* ---- console.css .btn / .btn.primary / .btn.danger ---------------------- */
enum { BTN_PLAIN, BTN_PRIMARY, BTN_DANGER };
Text btn_label(cairo_t *cr, const char *s);            /* 13.5px 500 */
double btn_height(cairo_t *cr);                        /* 9 + line + 9 + 2 */
double btn_width(const Text *label);                   /* 15 + w + 15 + 2 */
void draw_btn(App *a, cairo_t *cr, Rect b, Text *label, int kind, int id);

/* ---- console.css input[type=text|password|number], select ---------------- */
double field_height(cairo_t *cr);                      /* 10 + line(14px) + 10 + 2 */
void draw_field(App *a, cairo_t *cr, Rect r, const char *value, int mask,
                const char *placeholder, int id);
void draw_select(App *a, cairo_t *cr, Rect r, const char *shown, int id);
/* The open list of a <select>: drawn last so it is topmost; registers
 * HIT_OPTION_BASE + i for each row. */
void draw_popup(App *a, cairo_t *cr, Rect anchor, const char *const *opts, int n, int sel);

/* ---- text roles --------------------------------------------------------- */
Text label_make(cairo_t *cr, const char *s, double w);   /* label: 12px 500 muted */
/* .sub has NO rule in console.css or station.css (only .metric .sub, which
 * is not an ancestor here): a <p class="sub"> in the station page is body
 * text -- 14px, ink. Reproduced as the browser shows it, in one place. */
Text sub_make(cairo_t *cr, const char *s, double w);
Text error_make(cairo_t *cr, const char *s, double w);   /* .error: 12.5px danger */
#define ERROR_MIN_H 18.0                                 /* .error min-height */

/* ---- the card (console.css .sheet / .shead / .sbody / .sfoot) ------------ */
typedef struct { Text h3, p; double h; } Head;
Head head_make(cairo_t *cr, const char *h3, const char *p, double inner_w);
double card_width(double W);                             /* min(680px, 94vw) */
/* Shadow, surface, head, clip to the card; height clamped to 92vh (.sheet
 * max-height). *body is the .sbody box (possibly shorter than body_h --
 * overflow is clipped, there is no scrolling), *foot the .sfoot box. */
Rect card_frame(App *a, cairo_t *cr, double W, double H, double head_h, Head *hd,
                double body_h, double foot_h, Rect *body, Rect *foot);
void card_end(App *a, cairo_t *cr);
void body_clip_begin(App *a, cairo_t *cr, Rect body);
void body_clip_end(App *a, cairo_t *cr);
double foot_height(cairo_t *cr, double content_h);       /* 1 + 14 + content + 14 */
void foot_draw_bg(cairo_t *cr, const Theme *t, Rect foot);

/* ---- station.css pieces ------------------------------------------------- */
void draw_tray(cairo_t *cr, const Theme *t, double x, double y, int multi);
/* .bar.big-bar with a Progress.view state: pct 0..100, or -1 unknown
 * (moving = indeterminate stripes at 40%, else unknown-idle at 25%). */
void draw_big_bar(cairo_t *cr, const Theme *t, Rect r, int pct, int moving);
void draw_cls_bars(cairo_t *cr, const Theme *t, double x, double y, double w, int current);
void draw_toast(App *a, cairo_t *cr, double W, double H);

/* station.js fmtBytes / progress.js Progress.view label, ported. */
void fmt_bytes(char *out, size_t n, double v);
void progress_label(char *out, size_t n, int pct, int moving, int partition);

#endif
