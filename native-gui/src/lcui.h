/* #1322: the local clone's functions -- lc_state.c (the records, the roles,
 * the keys and clicks) and the three drawing files. ui.h has the data. */
#ifndef IMAGECTL_LCUI_H
#define IMAGECTL_LCUI_H
#include <cairo.h>
#include "input.h"
#include "ui.h"

/* one key=value record of the state file; 1 = it was an lc_ one */
int lc_state_line(State *s, const char *key, char *val);
/* after every reload: the default roles, the source to measure */
void lc_after_reload(App *a);
/* every input event while a local clone screen is up; 1 = taken */
int lc_handle(App *a, const Event *e);
/* the shared rules (drawing and keys use the same ones) */
int lc_view(const App *a);
int lc_role_of(const App *a, const char *dev);
int lc_source_index(const App *a);
const LcFit *lc_fit_of(const App *a, const char *dev);
int lc_target_ok(const App *a, int i);            /* disk i may be a target now */
int lc_targets(const App *a, int *idx, int max);  /* the chosen targets, by index */
int lc_can_go(const App *a);                      /* >= 1 target, every one allowed */
int lc_any_shrink(const App *a);
const LcDisk *lc_disk_by_dev(const App *a, const char *dev);
void lc_gb(char *out, size_t n, double bytes);    /* "149 GB", "610 MB" */
void lc_pct(char *out, size_t n, double share);   /* #1437: "0.2%", "37%" -- floored */
void lc_rate_sample(LcUi *u, int elapsed, long long copied);   /* #1437: one per reload */
double lc_rate(const LcUi *u, int *span);         /* bytes/s over the window, -1 = none */
int lc_unmeasured(const LcState *l);              /* #1440: a partition not measured, 0 = none */
void lc_disk_label(const LcDisk *d, char *out, size_t n);   /* "דיסק 3", or the device */
void lc_has_text(const LcDisk *d, char *out, size_t n);     /* "Windows · 3 מחיצות · 212 GB בשימוש" */
void lc_sample(State *s, const char *card);       /* --png: the mockup's scenario */
int lc_png_setup(App *a, const char *card);       /* 1 = card is a local clone card */
extern const char *const LC_PNG_CARDS[];
extern const int LC_PNG_NCARDS;

/* drawing */
void screen_lcboot(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_local(App *a, cairo_t *cr, double W, double H, double head_h);
void lc_draw_source(App *a, cairo_t *cr, double W, double H, double head_h);
void lc_draw_confirm(App *a, cairo_t *cr, double W, double H);
void lc_draw_progress(App *a, cairo_t *cr, double W, double H, double head_h);
void lc_draw_stop(App *a, cairo_t *cr, double W, double H);
void lc_draw_done(App *a, cairo_t *cr, double W, double H, double head_h);
const char *lc_status_word(const App *a, Rgb *dot);
const char *lc_round_note(const App *a);          /* #1367: a round is open on the server; NULL = say nothing */
const char *lc_status_keys(const App *a);
/* shared pieces (screens_local.c) */
double lc_btn(App *a, cairo_t *cr, double right, double y, double h, const char *label, const char *key,
              int kind, int id, int focus);
double lc_btn_width(cairo_t *cr, const char *label, const char *key);
void lc_dialog_head(App *a, cairo_t *cr, Rect box, const char *title, const char *lead, double *y);
Rect lc_dialog_box(App *a, cairo_t *cr, double W, double H, double w, double h);
#endif
