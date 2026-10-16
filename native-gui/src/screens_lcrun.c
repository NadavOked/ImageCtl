/* #1322: the local clone running and done -- mockup states 4 and 5
 * (localclone.html .ptop/.mets/.parts/.tg and .res). Every number is what
 * the agent measured (lcgui.sh lc_run/lc_part/lc_tgt): the bytes read from
 * the source, the bytes each target took, the partitions whose xxh64 came
 * back equal. Speed and time left are those bytes over the time passed --
 * there is no estimate before the copy starts. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "screens_rounds.h"

static const LcTgt *tgt_of(const App *a, int i) { return &a->st.lc.tgts[i]; }
static int tfailed(const LcTgt *t) { return !strcmp(t->state, "failed"); }

static void mmss(int s, char *out, size_t n) {
    if (s < 0) s = 0;
    if (s >= 3600) snprintf(out, n, "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60);
    else snprintf(out, n, "%02d:%02d", s / 60, s % 60);
}

/* #1437/#1440: the time left only from a total that was measured and a full
 * window of the copy's own pace; otherwise it says which of those is missing. */
static void left_text(const LcState *l, int um, double rate, int span, char *out, size_t n) {
    if (um) snprintf(out, n, "הזמן שנותר לא ידוע — מחיצה %d לא נמדדה", um);
    else if (l->total < 0) snprintf(out, n, "הזמן שנותר לא ידוע — הסך לא נמדד");
    else if (span < LC_RATE_WIN) snprintf(out, n, "מחשב את הזמן שנותר…");
    else if (rate <= 0) snprintf(out, n, "הזמן שנותר לא ידוע — ההעתקה לא מתקדמת");
    else {
        double secs = ((double)l->total - (double)l->copied) / rate;
        if (secs < 60) snprintf(out, n, "פחות מדקה נותרה");
        else if (secs < 90) snprintf(out, n, "פחות מ-2 דק' נותרו");
        else snprintf(out, n, "כ-%.0f דק' נותרו", secs / 60);
    }
}

static const LcDisk *disk_of(const App *a, const char *dev) { return lc_disk_by_dev(a, dev); }

static void label_dev(const App *a, const char *dev, char *out, size_t n) {
    const LcDisk *d = disk_of(a, dev); if (d) lc_disk_label(d, out, n); else snprintf(out, n, "%s", dev);
}

/* .met: a metric box -- label, value, the small line under it */
static void met(App *a, cairo_t *cr, Rect r, const char *l, const char *v, const char *h, Rgb hc) {
    const Theme *t = a->theme;
    draw_fill_rrect(cr, r, 4, t->surface); draw_border_rrect(cr, r, 4, t->hair, 1);
    int latin = (unsigned char)v[0] < 0x80;   /* "163 MB/s", "118 / 149 GB": one left-to-right value */
    Text tl = text_make(cr, FONT_SANS, 12, 400, l, 0, DIR_RTL), tv = latin ? text_make_ltr(cr, FONT_SANS, 20, 600, v, 0) : text_make(cr, FONT_SANS, 20, 600, v, 0, DIR_RTL);
    double right = r.x + r.w - 14;
    text_draw_r(cr, &tl, right, r.y + 11, t->muted); text_draw_r(cr, &tv, right, r.y + 11 + tl.h + 1, t->ink);
    if (h && h[0]) { Text th = text_make(cr, FONT_SANS, 11, 400, h, (int)(r.w - 28), DIR_RTL); text_draw_r(cr, &th, right, r.y + 11 + tl.h + 1 + tv.h + 1, hc); text_free(&th); }
    text_free(&tl); text_free(&tv);
}

/* a partition's weight in the row: its used bytes, or its size when they were not measured (#1440) */
static long long layout_bytes(const LcPart *p) { return p->bytes >= 0 ? p->bytes : p->size; }

static void part_box(App *a, cairo_t *cr, Rect r, const LcPart *p) {
    const Theme *t = a->theme; int done = !strcmp(p->state, "done"), now = !strcmp(p->state, "copy"), bad = !strcmp(p->state, "failed");
    draw_fill_rrect(cr, r, 4, t->surface); draw_border_rrect(cr, r, 4, now ? t->indigo : bad ? t->danger : t->hair, 1);
    double share = done ? 1 : now && p->bytes > 0 ? fmin(1, (double)p->in / (double)p->bytes) : 0;
    char head[64], st[48], d[160], b1[48], b2[48];
    snprintf(head, sizeof head, "%d · %s", p->idx, p->label);
    if (done) snprintf(st, sizeof st, "✓ הועתקה");
    else if (now && p->bytes < 0) lc_gb(st, sizeof st, (double)p->in);   /* #1440: no share of an unmeasured whole */
    else if (now) lc_pct(st, sizeof st, share);
    else if (bad) snprintf(st, sizeof st, "נכשלה"); else snprintf(st, sizeof st, "ממתינה");
    lc_gb(b1, sizeof b1, (double)p->bytes); lc_gb(b2, sizeof b2, (double)p->size);
    const char *fs = !strcmp(p->fs, "vfat") ? "FAT32" : !strcmp(p->fs, "ntfs") ? "NTFS" : p->fs[0] && strcmp(p->fs, "unknown") ? p->fs : NULL;
    if (!strcmp(p->fs, "swap")) snprintf(d, sizeof d, "swap · נוצרת מחדש");
    else if (!fs) snprintf(d, sizeof d, "ללא מ״ק · %s · מועתקת מלאה", b2);
    else if (p->bytes < 0) snprintf(d, sizeof d, "%s · %s · בשימוש: לא נמדד", fs, b2);   /* #1440 */
    else if (p->bytes < p->size) snprintf(d, sizeof d, "%s · %s בשימוש מתוך %s", fs, b1, b2);
    else snprintf(d, sizeof d, "%s · %s בשימוש", fs, b1);
    Text th = text_make(cr, FONT_SANS, 12, 600, head, 0, DIR_RTL), ts = text_make(cr, FONT_SANS, 12, 500, st, 0, DIR_RTL);
    Text td = text_make(cr, FONT_SANS, 11, 400, d, (int)(r.w - 24), DIR_RTL);
    pango_layout_set_ellipsize(td.layout, PANGO_ELLIPSIZE_END); pango_layout_set_height(td.layout, 0);
    double right = r.x + r.w - 12;
    text_draw_r(cr, &th, right, r.y + 8, t->ink); text_draw(cr, &ts, r.x + 12, r.y + 8, t->muted);
    text_draw_r(cr, &td, right, r.y + 8 + th.h + 2, t->muted);
    Rect bar = { r.x + 12, r.y + r.h - 9 - 4, r.w - 24, 4 };
    draw_fill_rrect(cr, bar, 2, t->track);
    if (share > 0) draw_fill_rrect(cr, (Rect){ bar.x + bar.w * (1 - share), bar.y, bar.w * share, 4 }, 2, done ? t->led_ok : t->led_write);
    text_free(&th); text_free(&ts); text_free(&td);
}

/* .tg .bay: one target while it is written */
static void tbay(App *a, cairo_t *cr, Rect b, const LcTgt *g) {
    const Theme *t = a->theme; const LcDisk *d = disk_of(a, g->dev);
    int bad = tfailed(g), ok = !strcmp(g->state, "done");
    double cy = b.y + b.h / 2;
    cairo_save(cr); draw_rrect_path(cr, b, 4); cairo_clip(cr); draw_set(cr, t->surface); cairo_paint(cr);
    cairo_rectangle(cr, b.x + b.w - 96, b.y, 1, b.h); cairo_rectangle(cr, b.x + 48, b.y, 1, b.h); draw_set(cr, t->hair); cairo_fill(cr);
    if (bad) { cairo_rectangle(cr, b.x + b.w - 4, b.y, 4, b.h); draw_set(cr, t->danger); cairo_fill(cr); }
    cairo_restore(cr); draw_border_rrect(cr, b, 4, bad ? t->danger : t->hair, 1);
    char num[16]; snprintf(num, sizeof num, "%d", d && d->port > 0 ? d->port : 0);
    Text nb = text_make(cr, FONT_MONO, 40, 600, num, 0, DIR_LTR), nl = text_make(cr, FONT_SANS, 11, 400, "מגירה", 0, DIR_RTL);
    double nr = b.x + b.w - 18, ny = cy - (46 + nl.h) / 2;
    text_draw_r(cr, &nb, nr, ny + (40 - nb.h) / 2, t->ink); text_draw_r(cr, &nl, nr, ny + 46, t->muted); text_free(&nb); text_free(&nl);
    double fr = b.x + b.w - 96 - 1 - 22, fl = b.x + 48 + 1 + 22;
    char name[48], sub[160], sz[32]; label_dev(a, g->dev, name, sizeof name);
    if (d) { disk_size(d->size, sz, sizeof sz); char mi[96]; bidi_isolate(d->model[0] ? d->model : d->dev, mi, sizeof mi); snprintf(sub, sizeof sub, "%s · %s", sz, mi); }
    else snprintf(sub, sizeof sub, "%s", g->dev);
    Text tn = text_make(cr, FONT_SANS, 18, 600, name, 0, DIR_RTL), tsb = text_make(cr, FONT_SANS, 12, 400, sub, 220, DIR_RTL);
    double bh = tn.h + 3 + tsb.h;
    text_draw_r(cr, &tn, fr, cy - bh / 2, t->ink); text_draw_r(cr, &tsb, fr, cy - bh / 2 + tn.h + 3, t->muted);
    if (bad || ok) sb_pill(cr, fr - tn.w - 10, cy - bh / 2 + tn.h / 2, bad ? "נכשל" : "זהה למקור", bad ? t->danger : t->led_ok, bad ? t->danger_soft : t->success_soft);
    text_free(&tn); text_free(&tsb);
    /* .prog */
    double px = fl, pw = fr - 220 - 16 - fl;
    double share = g->total > 0 ? fmin(1, (double)g->bytes / (double)g->total) : 0;
    if (ok) share = 1;
    char line[460], pct[24];
    if (ok || g->total > 0) lc_pct(pct, sizeof pct, share);
    else snprintf(pct, sizeof pct, "%.1f GB", (double)g->bytes / 1e9);   /* #1440: the total was not measured */
    const LcPart *cp = NULL; for (int i = 0; i < a->st.lc.nparts; i++) if (a->st.lc.parts[i].idx == g->part) cp = &a->st.lc.parts[i];
    if (bad) snprintf(line, sizeof line, "נכשל: %s", g->error);
    else if (ok) snprintf(line, sizeof line, "הושלם · נבדק");
    else if (!strcmp(g->state, "verifying")) snprintf(line, sizeof line, "בודק את טבלת המחיצות שנכתבה…");
    else if (cp) snprintf(line, sizeof line, "כותב מחיצה %d · %s · xxh64 תואם ב-%d מתוך %d", cp->idx, cp->label, g->ok, a->st.lc.nparts);
    else snprintf(line, sizeof line, "מכין את טבלת המחיצות…");
    Text tp = text_make(cr, FONT_MONO, 14, 600, pct, 0, DIR_LTR);
    Text tl = text_make(cr, FONT_SANS, 12, bad ? 600 : 400, line, (int)(pw - tp.w - 10), DIR_RTL);
    pango_layout_set_ellipsize(tl.layout, PANGO_ELLIPSIZE_END); pango_layout_set_height(tl.layout, 0);
    double ry = cy - (fmax(tl.h, tp.h) + 6 + 8) / 2;
    text_draw_r(cr, &tl, px + pw, ry, bad ? t->danger_fg : t->muted); text_draw(cr, &tp, px, ry, t->ink);
    Rect bar = { px, ry + fmax(tl.h, tp.h) + 6, pw, 8 };
    draw_fill_rrect(cr, bar, 4, t->track);
    if (share > 0) draw_fill_rrect(cr, (Rect){ bar.x + bar.w * (1 - share), bar.y, bar.w * share, 8 }, 4, bad ? t->danger : ok ? t->led_ok : t->led_write);
    text_free(&tp); text_free(&tl);
    double gx = b.x + 24;
    sb_led(cr, gx, cy - 15, 10, bad ? t->danger : ok ? t->led_ok : t->led_write, bad ? t->danger_soft : ok ? t->success_soft : t->indigo_soft, 1, 1);
    for (int k = 0; k < 2; k++) draw_fill_rrect(cr, (Rect){ gx - 10, cy + 2 + k * 14, 20, 3 }, 1.5, t->mark_line);
}

void lc_draw_progress(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t = a->theme; const LcState *l = &a->st.lc;
    double cw = fmin(980, W - 48), x0 = (W - cw) / 2, xr = x0 + cw, y = head_h + 22;
    char sl[48], lb[200], h1[200], s2[200]; const LcDisk *src = disk_of(a, l->run_src);
    label_dev(a, l->run_src, sl, sizeof sl);
    char mi[96]; bidi_isolate(src && src->model[0] ? src->model : l->run_src, mi, sizeof mi);
    snprintf(lb, sizeof lb, "שיכפול מקומי · מקור: %s (%s)", sl, mi);
    int nf = 0, alive = 0; for (int i = 0; i < l->ntgts; i++) { if (tfailed(tgt_of(a, i))) nf++; else alive++; }
    char names[160] = ""; int k = 0;
    for (int i = 0; i < l->ntgts; i++) if (!tfailed(tgt_of(a, i))) { char n2[48]; label_dev(a, l->tgts[i].dev, n2, sizeof n2);
        k += snprintf(names + k, sizeof names - k > 0 ? sizeof names - k : 0, "%s%s", k ? "ול" : "ל", n2); }
    if (!l->has_run) { snprintf(h1, sizeof h1, "מתחיל…"); snprintf(s2, sizeof s2, "בודק שוב את המקור ואת היעדים לפני הכתיבה."); }
    else if (!strcmp(l->phase, "shrinking")) { snprintf(h1, sizeof h1, "מכווץ זמנית את המקור…"); snprintf(s2, sizeof s2, "המחיצות יוחזרו לגודלן בסוף ההעתקה."); }
    else if (!strcmp(l->phase, "verifying")) { snprintf(h1, sizeof h1, "בודק את היעדים…"); snprintf(s2, sizeof s2, "טבלת המחיצות נקראת בחזרה מכל יעד."); }
    else if (!strcmp(l->phase, "restoring")) { snprintf(h1, sizeof h1, "מחזיר את המקור לגודלו…"); snprintf(s2, sizeof s2, "ההעתקה הסתיימה."); }
    else if (!strcmp(l->phase, "stopped")) { snprintf(h1, sizeof h1, "השיכפול נעצר"); snprintf(s2, sizeof s2, "היעדים כתובים בחלקם ואינם שמישים."); }
    else if (nf && alive) { snprintf(h1, sizeof h1, "ממשיך להעתיק %s", names);
        char fn[48] = ""; for (int i = 0; i < l->ntgts; i++) if (tfailed(tgt_of(a, i))) label_dev(a, l->tgts[i].dev, fn, sizeof fn);
        snprintf(s2, sizeof s2, "%s נכשל ונעצר. השאר לא הושפעו.", fn); }
    else if (!alive) { snprintf(h1, sizeof h1, "כל היעדים נכשלו"); snprintf(s2, sizeof s2, "המקור לא שונה."); }
    else { snprintf(h1, sizeof h1, "מעתיק %s%s", names, alive > 1 ? " במקביל" : ""); snprintf(s2, sizeof s2, "המקור נקרא פעם אחת ונכתב לכל היעדים יחד."); }
    /* #1440: one partition not measured and the whole is not known -- no percent of it, no time left */
    int um = lc_unmeasured(l), whole = !um && l->total > 0, span;
    double rate = lc_rate(&a->lcu, &span);   /* #1437: the window, not the average since the start */
    char big[24], lt[96];
    if (whole) lc_pct(big, sizeof big, (double)l->copied / l->total); else snprintf(big, sizeof big, "—");
    left_text(l, um, rate, span, lt, sizeof lt);
    Text tlb = text_make(cr, FONT_SANS, 12, 500, lb, 0, DIR_RTL), th1 = text_make(cr, FONT_SANS, 26, 600, h1, (int)(cw - 260), DIR_RTL);
    Text ts2 = text_make(cr, FONT_SANS, 13, 400, s2, (int)(cw - 260), DIR_RTL);
    Text tb = text_make(cr, FONT_MONO, 64, 400, big, 0, DIR_LTR), tlt = text_make(cr, FONT_SANS, 13, 400, lt, 0, DIR_RTL);
    double hh = fmax(tlb.h + 2 + th1.h + 3 + ts2.h, tb.h + tlt.h);
    double ty = y + hh - (tlb.h + 2 + th1.h + 3 + ts2.h);
    text_draw_r(cr, &tlb, xr, ty, t->muted); ty += tlb.h + 2; text_draw_r(cr, &th1, xr, ty, t->ink); ty += th1.h + 3; text_draw_r(cr, &ts2, xr, ty, t->muted);
    text_draw(cr, &tb, x0, y + hh - tlt.h - tb.h, t->ink); text_draw(cr, &tlt, x0 + tb.w - tlt.w, y + hh - tlt.h, t->muted);
    text_free(&tlb); text_free(&th1); text_free(&ts2); text_free(&tb); text_free(&tlt);
    y += hh + 16;
    /* .mets -- speed (right), copied, time passed (left) */
    double mw = (cw - 24) / 3, mh = 80;
    char v1[48], v2[160], v3[32], c1[48], c2[48], hint[160] = "", ch[96] = "בלוקים בשימוש בלבד";
    if (span >= LC_RATE_MIN && rate >= 0) snprintf(v1, sizeof v1, "%.0f MB/s", rate / 1e6); else snprintf(v1, sizeof v1, "עוד לא נמדדה");
    double gc = l->copied / 1e9, gt = whole ? l->total / 1e9 : gc;
    snprintf(c1, sizeof c1, gt >= 100 ? "%.0f" : gt >= 10 ? "%.1f" : "%.2f", gc); snprintf(c2, sizeof c2, gt >= 100 ? "%.0f" : gt >= 10 ? "%.1f" : "%.2f", gt);
    if (whole) snprintf(v2, sizeof v2, "%s / %s GB", c1, c2); else snprintf(v2, sizeof v2, "%s GB", c1);
    if (um) snprintf(ch, sizeof ch, "הסך לא ידוע — מחיצה %d לא נמדדה", um);
    else if (!whole) snprintf(ch, sizeof ch, "הסך לא ידוע — לא נמדד");
    mmss(l->elapsed, v3, sizeof v3);
    if (nf && alive) snprintf(hint, sizeof hint, "היעד שנכשל יצא מהזרם — הוא כבר לא מאט אותו");
    met(a, cr, (Rect){ xr - mw, y, mw, mh }, "מהירות", v1, hint, t->led_ok);
    met(a, cr, (Rect){ xr - 2 * mw - 12, y, mw, mh }, "הועתקו", v2, ch, um || !whole ? t->warn : t->muted);
    met(a, cr, (Rect){ x0, y, mw, mh }, "זמן שעבר", v3, "", t->muted);
    y += mh + 12;
    /* .parts -- the biggest partition gets 2.4 shares, as the mockup's Windows */
    if (l->nparts) {
        int bigi = 0; for (int i = 1; i < l->nparts; i++) if (layout_bytes(&l->parts[i]) > layout_bytes(&l->parts[bigi])) bigi = i;
        double shares = l->nparts - 1 + 2.4, unit = (cw - 6 * (l->nparts - 1)) / shares, px = xr;
        for (int i = 0; i < l->nparts; i++) {
            double w = unit * (i == bigi ? 2.4 : 1);
            part_box(a, cr, (Rect){ px - w, y, w, 54 }, &l->parts[i]); px -= w + 6;
        }
        y += 54 + 12;
    }
    /* the targets */
    int n = l->ntgts; double bh = 96, room = H - N_STATUS_H - 16 - 40 - 12 - y - (10 + 16 + 8 + 10) - (n > 1 ? (n - 1) * 10 : 0);
    if (n && n * bh > room) bh = fmax(64, floor(room / n));
    Rect cage = { x0, y, cw, 10 + 16 + 8 + n * bh + (n > 1 ? (n - 1) * 10 : 0) + 10 };
    draw_fill_rrect(cr, cage, 6, t->surface); draw_border_rrect(cr, cage, 6, t->hair, 1);
    char cnt[64]; snprintf(cnt, sizeof cnt, "%d מתוך %d כותבים", !strcmp(l->phase, "done") ? 0 : alive, n);
    Text a1 = text_make(cr, FONT_SANS, 12, 400, "יעדים", 0, DIR_RTL), a2 = text_make(cr, FONT_SANS, 12, 400, cnt, 0, DIR_RTL);
    text_draw_r(cr, &a1, xr - 14, y + 10, t->muted); text_draw(cr, &a2, x0 + 14, y + 10, t->muted); text_free(&a1); text_free(&a2);
    double by = y + 10 + 16 + 8;
    for (int i = 0; i < n; i++) { tbay(a, cr, (Rect){ x0 + 10, by, cw - 20, bh }, &l->tgts[i]); by += bh + 10; }
    y = cage.y + cage.h + 12;
    int live = !strcmp(l->phase, "copying") || !strcmp(l->phase, "shrinking");
    lc_btn(a, cr, x0 + lc_btn_width(cr, "עצור", "Esc"), y, 40, "עצור", "Esc", live ? BTN_PLAIN : BTN_OFF, HIT_LC_STOP, live && !a->lcu.stop && a->lcu.focus == LCF_STOP);
    Text fm = text_make(cr, FONT_SANS, 13, 400, "כשל ביעד אחד לא עוצר את האחרים.", 0, DIR_RTL);
    text_draw_r(cr, &fm, xr, y + 20 - fm.h / 2, t->muted); text_free(&fm);
    if (a->lcu.stop && live) lc_draw_stop(a, cr, W, H);
}

void lc_draw_stop(App *a, cairo_t *cr, double W, double H) {
    double w = fmin(560, W - 48);
    const char *lead = a->st.lc.shrunk ? "היעדים יישארו כתובים בחלקם, ולא יהיה אפשר להפעיל מהם מחשב. המקור יוחזר לגודלו. אפשר לשכפל שוב אחר כך."
                                       : "היעדים יישארו כתובים בחלקם, ולא יהיה אפשר להפעיל מהם מחשב. המקור לא ישתנה. אפשר לשכפל שוב אחר כך.";
    Text th = text_make(cr, FONT_SANS, 22, 600, "לעצור את השיכפול?", (int)(w - 98), DIR_RTL), tl = text_make(cr, FONT_SANS, 14, 400, lead, (int)(w - 98), DIR_RTL);
    double h = 26 + fmax(30, th.h) + 6 + tl.h + 16 + 4 + 44 + 24; text_free(&th); text_free(&tl);
    Rect box = lc_dialog_box(a, cr, W, H, w, h);
    double y = box.y + 26, right = box.x + box.w - 28;
    lc_dialog_head(a, cr, box, "לעצור את השיכפול?", lead, &y);
    y += 4;
    double gw = lc_btn(a, cr, right, y, 44, "עצור את השיכפול", NULL, BTN_DANGER, HIT_LC_STOP_GO, a->lcu.focus == LCF_STOP_GO);
    lc_btn(a, cr, right - gw - 10, y, 44, "המשך להעתיק", "Esc", BTN_PLAIN, HIT_LC_STOP_NO, a->lcu.focus == LCF_STOP_NO);
}

/* ---- the result ----------------------------------------------------------------------- */

static double res_card(App *a, cairo_t *cr, double x, double y, double w, const LcTgt *g, int draw) {
    const Theme *t = a->theme; const LcDisk *d = disk_of(a, g->dev); int bad = tfailed(g);
    char name[48], h3[300], mono[160], li[6][460]; int mk[6], n = 0;
    label_dev(a, g->dev, name, sizeof name);
    if (d) { char sata[24]; if (d->port > 0) snprintf(sata, sizeof sata, "SATA %d", d->port - 1); else snprintf(sata, sizeof sata, "%s", d->dev);
        snprintf(mono, sizeof mono, "%s · %s", d->model[0] ? d->model : d->dev, sata); } else snprintf(mono, sizeof mono, "%s", g->dev);
    if (!bad) {
        char gb[48]; lc_gb(gb, sizeof gb, (double)g->grown_bytes);
        if (g->has_table && g->grown_idx > 0) snprintf(h3, sizeof h3, "%s · זהה למקור · המחיצה האחרונה הוגדלה ל-%s", name, gb);
        else snprintf(h3, sizeof h3, "%s · זהה למקור", name);
        snprintf(li[n], sizeof li[n], "%d מחיצות הועתקו · xxh64 של כל מחיצה תואם למה שנקרא מהמקור", g->ok); mk[n++] = 1;
        snprintf(li[n], sizeof li[n], "טבלת המחיצות נקראה בחזרה מהדיסק — זהה למקור, כולל מזהי GUID"); mk[n++] = 1;
        if (g->has_table && g->grown_idx > 0) {
            char was[48]; lc_gb(was, sizeof was, (double)g->src_bytes);
            snprintf(li[n], sizeof li[n], "מחיצה %d הוגדלה מ-%s ל-%s, עד סוף הדיסק; מה שאחריה הועבר לסוף, בגודלו", g->grown_idx, was, gb); mk[n++] = 0;
        } else if (g->has_table && g->free_bytes > 500000000LL) {
            char fr[48]; lc_gb(fr, sizeof fr, (double)g->free_bytes);
            snprintf(li[n], sizeof li[n], "%s בסוף הדיסק נשארו לא מוקצים (פחות מ-1 GiB מעל המקור — אין הגדלה)", fr); mk[n++] = 0;
        }
        if (g->error[0]) { snprintf(li[n], sizeof li[n], "%s", g->error); mk[n++] = 2; }   /* the grow was deferred (finish_grow) */
    } else {
        snprintf(h3, sizeof h3, "%s · נכשל", name);
        if (g->ok > 0) { snprintf(li[n], sizeof li[n], g->ok == 1 ? "מחיצה אחת הועתקה ונבדקה" : "%d מחיצות הועתקו ונבדקו", g->ok); mk[n++] = 1; }
        snprintf(li[n], sizeof li[n], "%s", g->error); mk[n++] = -1;
    }
    double inner = w - 44 - 54 - 18;
    Text th = text_make(cr, FONT_SANS, 20, 600, h3, (int)(inner - 200), DIR_RTL), tm = text_make(cr, FONT_MONO, 13, 500, mono, 0, DIR_LTR);
    Text tl[6]; double h = 18 + fmax(th.h, tm.h) + 8;
    for (int i = 0; i < n; i++) { tl[i] = text_make(cr, FONT_SANS, 13, 400, li[i], (int)(inner - 22), DIR_RTL); h += tl[i].h + (i ? 4 : 0); }
    const char *todo = !bad ? NULL : g->bytes > 0 || g->part > 0 ? "הדיסק לא יעלה ואינו שמיש. אל תכניסו אותו למחשב בכיתה — בדקו או החליפו אותו ושכפלו שוב."
                                                                 : "הדיסק לא נכתב — מה שהיה עליו נשאר.";
    Text tt = todo ? text_make(cr, FONT_SANS, 13, 400, todo, (int)(inner - 24), DIR_RTL) : (Text){ NULL, 0, 0 };
    if (todo) h += 8 + tt.h + 16;
    h += 18;
    if (draw) {
        Rect r = { x, y, w, h };
        draw_fill_rrect(cr, r, 6, t->surface); draw_border_rrect(cr, r, 6, bad ? t->danger : t->hair, 1);
        cairo_save(cr); draw_rrect_path(cr, r, 6); cairo_clip(cr); cairo_rectangle(cr, x + w - 4, y, 4, h); draw_set(cr, bad ? t->danger : t->led_ok); cairo_fill(cr); cairo_restore(cr);
        double right = x + w - 22, icx = right - 27, icy = y + 18 + 27;
        cairo_arc(cr, icx, icy, 27, 0, 2 * M_PI); draw_set(cr, bad ? t->danger_soft : t->success_soft); cairo_fill_preserve(cr);
        cairo_set_line_width(cr, 1); draw_set(cr, bad ? t->danger : t->led_ok); cairo_stroke(cr);
        cairo_set_line_width(cr, 3); cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
        if (bad) { cairo_move_to(cr, icx - 6, icy - 6); cairo_line_to(cr, icx + 6, icy + 6); cairo_move_to(cr, icx + 6, icy - 6); cairo_line_to(cr, icx - 6, icy + 6); }
        else { cairo_move_to(cr, icx - 8, icy + .5); cairo_line_to(cr, icx - 2.5, icy + 6); cairo_line_to(cr, icx + 8, icy - 5); }
        cairo_stroke(cr);
        double tx = right - 54 - 18, ty = y + 18;
        text_draw_r(cr, &th, tx, ty, t->ink); text_draw_r(cr, &tm, tx - th.w - 10, ty + (th.h - tm.h) / 2, t->muted);
        ty += fmax(th.h, tm.h) + 8;
        for (int i = 0; i < n; i++) {
            /* the marks are drawn: the font has no ✕, and a box is not an X */
            double mx = tx - 8, my = ty + 9; cairo_set_line_width(cr, 2);
            if (mk[i] == 1) { draw_set(cr, t->led_ok); cairo_move_to(cr, mx - 5, my); cairo_line_to(cr, mx - 2, my + 3); cairo_line_to(cr, mx + 4, my - 4); cairo_stroke(cr); }
            else if (mk[i] == -1) { draw_set(cr, t->danger); cairo_move_to(cr, mx - 4, my - 4); cairo_line_to(cr, mx + 4, my + 4); cairo_move_to(cr, mx + 4, my - 4); cairo_line_to(cr, mx - 4, my + 4); cairo_stroke(cr); }
            else { cairo_arc(cr, mx, my, 1.6, 0, 2 * M_PI); draw_set(cr, mk[i] == 2 ? t->warn : t->muted); cairo_fill(cr); }
            text_draw_r(cr, &tl[i], tx - 22, ty, mk[i] == 2 ? t->warn : t->ink); ty += tl[i].h + 4;
        }
        if (todo) { Rect tr = { x + 22, ty + 4, tx - x - 22, tt.h + 16 };
            draw_fill_rrect(cr, tr, 3, t->danger_soft); text_draw_r(cr, &tt, tx - 12, tr.y + 8, t->ink); }
    }
    text_free(&th); text_free(&tm); for (int i = 0; i < n; i++) text_free(&tl[i]);
    if (tt.layout) text_free(&tt);
    return h;
}

void lc_draw_done(App *a, cairo_t *cr, double W, double H, double head_h) {
    (void)H;
    const Theme *t = a->theme; const LcState *l = &a->st.lc;
    double cw = fmin(980, W - 48), x0 = (W - cw) / 2, xr = x0 + cw, y = head_h + 22;
    int nf = 0; for (int i = 0; i < l->ntgts; i++) nf += tfailed(tgt_of(a, i));
    char h1[160], p[300], sl[48], tot[48], el[32], mi[96];
    if (!nf) snprintf(h1, sizeof h1, "השיכפול הסתיים בהצלחה");
    else if (nf == l->ntgts) snprintf(h1, sizeof h1, "השיכפול נכשל — אף יעד לא הושלם");
    else snprintf(h1, sizeof h1, "השיכפול הסתיים — %d מתוך %d הצליחו", l->ntgts - nf, l->ntgts);
    const LcDisk *src = disk_of(a, l->run_src); label_dev(a, l->run_src, sl, sizeof sl);
    lc_gb(tot, sizeof tot, (double)(l->total >= 0 ? l->total : l->copied)); mmss(l->elapsed, el, sizeof el);   /* #1440: what was read */
    bidi_isolate(src && src->model[0] ? src->model : l->run_src, mi, sizeof mi);
    snprintf(p, sizeof p, RLM "%s · %s דקות · מקור: %s (%s)", tot, el, sl, mi);
    Text th = text_make(cr, FONT_SANS, 26, 600, h1, 0, DIR_RTL), tp = text_make(cr, FONT_SANS, 14, 400, p, 0, DIR_RTL);
    text_draw_r(cr, &th, xr, y, t->ink); y += th.h + 4; text_draw_r(cr, &tp, xr, y, t->muted); y += tp.h + 16;
    text_free(&th); text_free(&tp);
    for (int i = 0; i < l->ntgts; i++) y += res_card(a, cr, x0, y, cw, &l->tgts[i], 1) + 10;
    /* the source: read only, or shrunk for the copy and grown back -- or not, said in red */
    char sn[500]; int bad = l->src_restore[0] && strcmp(l->src_restore, "ok");
    if (bad) snprintf(sn, sizeof sn, "%s (המקור): %s", sl, l->src_restore);
    else if (l->shrunk) snprintf(sn, sizeof sn, "%s (המקור) כווץ זמנית לשיכפול והוחזר לגודלו המקורי.", sl);
    else snprintf(sn, sizeof sn, "%s (המקור) נקרא בלבד ולא שונה.", sl);
    Text ts = text_make(cr, FONT_SANS, 13, bad ? 600 : 400, sn, (int)(cw - 20), DIR_RTL);
    y += 2; sb_led(cr, xr - 5, y + 9, 10, bad ? t->danger : t->led_ok, bad ? t->danger_soft : t->success_soft, 0, 1);
    text_draw_r(cr, &ts, xr - 18, y, bad ? t->danger_fg : t->muted); y += ts.h + 6; text_free(&ts);
    const char *rp = !strcmp(l->report, "sent") ? "דווח ליומן השרת (דיווח המחשב — השיכפול לא עבר דרך השרת)."
                   : "לא דווח ליומן השרת — השרת לא ענה. השיכפול עצמו אינו תלוי בשרת.";
    Text tr = text_make(cr, FONT_SANS, 12, 400, rp, (int)(cw - 20), DIR_RTL);
    text_draw_r(cr, &tr, xr - 18, y, t->muted); y += tr.h + 16; text_free(&tr);
    double ow = lc_btn(a, cr, x0 + lc_btn_width(cr, "כבה את המחשב", "Enter"), y, 40, "כבה את המחשב", "Enter", BTN_PRIMARY, HIT_LC_OFF, a->lcu.focus == LCF_OFF);
    lc_btn(a, cr, x0 + ow + 10 + lc_btn_width(cr, "שכפול נוסף", NULL), y, 40, "שכפול נוסף", NULL, BTN_PLAIN, HIT_LC_AGAIN, a->lcu.focus == LCF_AGAIN);
}

void screen_local(App *a, cairo_t *cr, double W, double H, double head_h) {
    int v = lc_view(a);
    if (v == LCVIEW_DONE) lc_draw_done(a, cr, W, H, head_h);
    else if (v == LCVIEW_PROGRESS) lc_draw_progress(a, cr, W, H, head_h);
    else lc_draw_source(a, cr, W, H, head_h);
}
