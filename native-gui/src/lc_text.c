/* #1322: the local clone's words -- amounts, a disk's label and contents,
 * the status bar -- and the --png scenario (the approved mockup's: three
 * drawers, drawer 3 the source). */
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "screens_rounds.h"

/* An amount as an isolated LTR run (FSI ... PDI): "149 GB" inside a Hebrew
 * sentence stays "149 GB", never "GB 149". */
void lc_gb(char *out, size_t n, double b) {
    double g = b / 1e9;
    if (b < 0) snprintf(out, n, "לא נמדד");
    else if (g >= 100) snprintf(out, n, "\xE2\x81\xA8%.0f GB\xE2\x81\xA9", g);
    else if (g >= 10) snprintf(out, n, "\xE2\x81\xA8%.1f GB\xE2\x81\xA9", g);
    else if (g >= 1) snprintf(out, n, "\xE2\x81\xA8%.2f GB\xE2\x81\xA9", g);
    else snprintf(out, n, "\xE2\x81\xA8%.0f MB\xE2\x81\xA9", b / 1e6);
}

void lc_disk_label(const LcDisk *d, char *out, size_t n) {
    if (d->port > 0) snprintf(out, n, "דיסק %d", d->port);
    else snprintf(out, n, "%s", d->dev);
}

void lc_has_text(const LcDisk *d, char *out, size_t n) {
    char used[160], os[96], u[64];
    if (!d->nparts) { snprintf(out, n, "%s", d->why[0] ? d->why : "ריק"); return; }
    lc_gb(u, sizeof u, (double)d->used);
    bidi_isolate(d->os[0] ? d->os : "בלי מערכת מוכרת", os, sizeof os);
    if (d->used < 0) snprintf(used, sizeof used, "בשימוש: לא נמדד");
    else snprintf(used, sizeof used, "%s בשימוש", u);
    snprintf(out, n, RLM "%s · %d מחיצות · %s", os, d->nparts, used);   /* RLM: Pango's auto-dir does not skip an isolate */
}

static int failed_targets(const App *a) {
    int f = 0;
    for (int i = 0; i < a->st.lc.ntgts; i++) f += !strcmp(a->st.lc.tgts[i].state, "failed");
    return f;
}

const char *lc_status_word(const App *a, Rgb *dot) {
    static char buf[96]; const Theme *t = a->theme; const LcState *l = &a->st.lc;
    *dot = t->led_write;
    if (a->screen == SCREEN_LCBOOT) {
        if (l->boot_paused || a->lcu.pause_sent) { *dot = t->warn; return "ממתין לבחירה"; }
        return "סופר לאחור";
    }
    int view = lc_view(a), nf = failed_targets(a);
    if (view == LCVIEW_SOURCE) { *dot = t->warn; return l->inv_done ? "בוחרים מקור" : "בודק את הדיסקים"; }
    if (view == LCVIEW_DONE) {
        if (!nf) { *dot = t->led_ok; return "הסתיים"; }
        *dot = t->danger;
        if (nf == 1) return "הסתיים · יעד אחד נכשל";
        snprintf(buf, sizeof buf, "הסתיים · %d יעדים נכשלו", nf); return buf;
    }
    if (!strcmp(l->phase, "shrinking")) return "מכווץ את המקור זמנית";
    if (!strcmp(l->phase, "verifying")) return "בודק את היעדים";
    if (!strcmp(l->phase, "restoring")) return "מחזיר את המקור לגודלו";
    if (!strcmp(l->phase, "stopped")) { *dot = t->warn; return "נעצר"; }
    int alive = l->ntgts - nf;
    if (nf && alive) { *dot = t->warn; return alive == 1 ? "יעד אחד נכשל — השני ממשיך" : "יעד נכשל — האחרים ממשיכים"; }
    if (alive == 1) return "מעתיק ליעד אחד";
    if (alive == 2) return "מעתיק לשני יעדים";
    snprintf(buf, sizeof buf, "מעתיק ל-%d יעדים", alive); return buf;
}

const char *lc_status_keys(const App *a) {
    if (a->screen == SCREEN_LCBOOT) return "→ ←  מעבר    Enter  בחירה    1  מהשרת    2  שיכפול מקומי";
    int view = lc_view(a);
    if (view == LCVIEW_SOURCE) return a->lcu.confirm ? "Tab / → ←  מעבר    Enter  בחירה    Esc  חזרה"
                                                     : "↑ ↓  מגירה    → ←  תפקיד    1 2 3  קבע כמקור    Tab  כפתורים    Esc  חזרה";
    if (view == LCVIEW_PROGRESS) return a->lcu.stop ? "Tab / → ←  מעבר    Enter  בחירה    Esc  המשך להעתיק" : "Esc  עצירה";
    return "Enter  כיבוי    Tab  מעבר";
}

/* ---- --png: the mockup's scenario ------------------------------------------- */

const char *const LC_PNG_CARDS[] = { "lcboot", "lcboot-paused", "lcsource", "lcsource-measuring", "lcsource-blocked",
                                     "lcsource-shrink", "lcconfirm", "lcconfirm-shrink", "lcprogress", "lcprogress-fail",
                                     "lcstop", "lcdone", "lcdone-ok" };
const int LC_PNG_NCARDS = (int)(sizeof LC_PNG_CARDS / sizeof LC_PNG_CARDS[0]);

static void disk(LcState *l, int port, const char *dev, const char *model, unsigned long long size, const char *smart,
                 const char *why, const char *os, int nparts, long long used) {
    LcDisk *d = &l->disks[l->ndisks++];
    d->port = port; snprintf(d->dev, sizeof d->dev, "%s", dev); snprintf(d->model, sizeof d->model, "%s", model);
    d->size = size; snprintf(d->smart, sizeof d->smart, "%s", smart); snprintf(d->smart_reason, sizeof d->smart_reason, "%s", why);
    snprintf(d->os, sizeof d->os, "%s", os); d->nparts = nparts; d->used = used; d->need = (long long)size; d->usable = 1;
}

static void fit(LcState *l, const char *dev, int v, long long need, const char *reason) {
    LcFit *f = &l->fits[l->nfits++];
    snprintf(f->dev, sizeof f->dev, "%s", dev); f->verdict = v; f->need = need; snprintf(f->reason, sizeof f->reason, "%s", reason);
}

static void part(LcState *l, int idx, const char *label, const char *fs, long long bytes, long long size, const char *st, long long in) {
    LcPart *p = &l->parts[l->nparts++];
    p->idx = idx; snprintf(p->label, sizeof p->label, "%s", label); snprintf(p->fs, sizeof p->fs, "%s", fs);
    p->bytes = bytes; p->size = size; snprintf(p->state, sizeof p->state, "%s", st); p->in = in;
}

static void tgt(LcState *l, const char *dev, const char *st, long long bytes, int p, int ok, const char *err) {
    LcTgt *t = &l->tgts[l->ntgts++];
    snprintf(t->dev, sizeof t->dev, "%s", dev); snprintf(t->state, sizeof t->state, "%s", st);
    t->bytes = bytes; t->part = p; t->ok = ok; t->total = 148600000000LL; snprintf(t->error, sizeof t->error, "%s", err);
}

void lc_sample(State *s, const char *card) {
    LcState *l = &s->lc; memset(l, 0, sizeof *l);
    snprintf(s->machine_name, sizeof s->machine_name, "CLONER-02");
    if (!strncmp(card, "lcboot", 6)) {
        l->screen = LC_SCREEN_BOOT; l->boot_now = 1000; l->boot_deadline = 1017;
        l->boot_paused = !strcmp(card, "lcboot-paused");
        return;
    }
    l->screen = LC_SCREEN_LOCAL; l->inv_done = 1; snprintf(l->phase, sizeof l->phase, "idle");
    disk(l, 1, "sda", "Samsung SSD 870 EVO", 500107862016ULL, "ok", "ok", "Windows 10", 3, 212400000000LL);
    disk(l, 2, "sdb", "WDC WD5000AAKX", 500107862016ULL, "fail", "health_failed", "Ubuntu 22.04", 2, 38000000000LL);
    disk(l, 3, "sdc", "Crucial MX500", 480103981056ULL, "ok", "ok", "Windows 11 Pro", 4, 148600000000LL);
    int blocked = !strcmp(card, "lcsource-blocked"), shrink = !strcmp(card, "lcsource-shrink") || !strcmp(card, "lcconfirm-shrink");
    snprintf(l->src, sizeof l->src, "%s", blocked || shrink ? "sda" : "sdc");
    snprintf(l->src_measure, sizeof l->src_measure, "%s", blocked || shrink ? "done" : "none");
    if (!strcmp(card, "lcsource-measuring")) { snprintf(l->src, sizeof l->src, "sda"); snprintf(l->src_measure, sizeof l->src_measure, "measuring"); }
    if (blocked) { fit(l, "sdb", LCV_FITS, 500107862016LL, ""); fit(l, "sdc", LCV_SHORT, 490103981056LL, ""); }
    else if (shrink) { fit(l, "sdb", LCV_FITS, 500107862016LL, ""); fit(l, "sdc", LCV_SHRINK, 262400000000LL, ""); }
    else if (!strcmp(card, "lcsource-measuring")) { fit(l, "sdb", LCV_FITS, 500107862016LL, ""); fit(l, "sdc", LCV_MEASURING, 500107862016LL, ""); }
    else { fit(l, "sda", LCV_FITS, 480103981056LL, ""); fit(l, "sdb", LCV_FITS, 480103981056LL, ""); }
    if (strncmp(card, "lcprogress", 10) && strcmp(card, "lcstop") && strncmp(card, "lcdone", 6)) return;
    int fail = !strcmp(card, "lcprogress-fail") || !strcmp(card, "lcdone"), done = !strncmp(card, "lcdone", 6);
    snprintf(l->phase, sizeof l->phase, "%s", done ? "done" : "copying");
    l->has_run = 1; snprintf(l->run_src, sizeof l->run_src, "sdc"); l->total = 148600000000LL;
    l->copied = done ? l->total : fail ? 118400000000LL : 55100000000LL;
    l->elapsed = done ? (fail ? 1065 : 372) : fail ? 728 : 399; l->cur_part = 3;
    snprintf(l->report, sizeof l->report, "%s", done ? "sent" : "");
    long long in3 = l->copied - 32000000LL - 16000000LL;
    part(l, 1, "EFI", "vfat", 32000000LL, 104857600LL, "done", 0);
    part(l, 2, "MSR", "", 16000000LL, 16777216LL, "done", 0);
    part(l, 3, "Windows", "ntfs", 147942000000LL, 446000000000LL, done ? "done" : "copy", done ? 0 : in3);
    part(l, 4, "Recovery", "ntfs", 610000000LL, 650000000LL, done ? "done" : "wait", 0);
    tgt(l, "sda", done ? "done" : "writing", l->copied, done ? 0 : 3, done ? 4 : 2, "");
    if (done) { l->tgts[0].has_table = 1; l->tgts[0].grown_idx = 3; l->tgts[0].grown_bytes = 466000000000LL; l->tgts[0].src_bytes = 446000000000LL; }
    if (!strcmp(card, "lcdone-ok")) return;
    if (fail) tgt(l, "sdb", "failed", 91600000000LL, 3, 2, "מחיצה 3: שגיאת כתיבה אחרי 91.6 GB — fanout: write: Input/output error · partclone rc=1");
    else tgt(l, "sdb", "writing", l->copied, 3, 2, "");
}

int lc_png_setup(App *a, const char *card) {
    if (strncmp(card, "lc", 2)) return 0;
    lc_sample(&a->st, card);
    memset(&a->lcu, 0, sizeof a->lcu);
    a->force_cloner = 1;
    if (!strcmp(card, "lcboot-paused")) { a->lcu.boot_focus = 1; a->lcu.pause_sent = 1; }
    if (a->st.lc.screen != LC_SCREEN_LOCAL) return 1;
    lc_after_reload(a);
    LcUi *u = &a->lcu;
    if (!strcmp(card, "lcsource-blocked") || !strcmp(card, "lcsource-shrink") || !strcmp(card, "lcconfirm-shrink") || !strcmp(card, "lcsource-measuring")) {
        u->nroles = 0;                     /* drawer 1 chosen as the source by the person */
        const char *dev[3] = { "sda", "sdb", "sdc" }; int r[3] = { LCR_SOURCE, LCR_TARGET, LCR_TARGET };
        for (int i = 0; i < 3; i++) { snprintf(u->role_dev[i], sizeof u->role_dev[i], "%s", dev[i]); u->role[i] = r[i]; }
        u->nroles = 3; u->bay = 0;
    }
    if (!strcmp(card, "lcconfirm") || !strcmp(card, "lcconfirm-shrink")) {
        for (int i = 0; i < u->nroles; i++) if (!strcmp(u->role_dev[i], "sdb")) u->role[i] = LCR_TARGET;   /* the person kept disk 2 */
        u->confirm = 1; u->focus = LCF_CANCEL;
    }
    if (!strcmp(card, "lcprogress") || !strcmp(card, "lcprogress-fail")) u->focus = LCF_STOP;
    if (!strcmp(card, "lcstop")) { u->stop = 1; u->focus = LCF_STOP_NO; }
    if (!strncmp(card, "lcdone", 6)) u->focus = LCF_OFF;
    return 1;
}
