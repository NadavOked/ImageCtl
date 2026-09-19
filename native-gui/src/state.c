#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "state.h"

static char EMPTY[1] = "";

/* "a|b||d" -> f[0..3], missing fields are "" -- never NULL. */
static int split(char *v, char *f[], int max) {
    int n = 0;
    f[n++] = v;
    for (char *p = v; *p && n < max; p++)
        if (*p == '|') { *p = 0; f[n++] = p + 1; }
    for (int i = n; i < max; i++) f[i] = EMPTY;
    return n;
}

static void cp(char *dst, size_t n, const char *src) { snprintf(dst, n, "%s", src); }
static int  num(const char *s, int dflt) { return s[0] ? atoi(s) : dflt; }

/* A strict non-negative integer: the whole field, digits only, no sign, no
 * leading space, no trailing junk, in range. Returns 0 on any of those so a
 * corrupt state record is rejected rather than folded to a wrong 0/huge value
 * (atoi/strtoull silently accept " -1", "12junk"). */
static int parse_uint(const char *s, unsigned long long *out) {
    if (s[0] < '0' || s[0] > '9') return 0;          /* empty, sign or space */
    errno = 0;
    char *end;
    unsigned long long v = strtoull(s, &end, 10);
    if (errno != 0 || *end != '\0') return 0;
    *out = v;
    return 1;
}

/* #695: room-grid helpers. */
static Machine *machine_at(State *s, unsigned long long index) {
    if (index >= (unsigned long long)s->nmachines) return NULL;
    return &s->machines[index];
}

/* A legacy agent may send null port as 0. Give it the first unused physical
 * slot so it stays visible and deterministic, without pretending the server
 * identified a SATA port. */
static int room_fallback_port(const Machine *m) {
    for (int port = 1; port <= m->drawer_count; port++) {
        int used = 0;
        for (int i = 0; i < m->nroom_drawers; i++)
            if (m->room_drawers[i].port == port) { used = 1; break; }
        if (!used) return port;
    }
    return 0;
}

void state_parse(State *s, FILE *fp) {
    memset(s, 0, sizeof *s);
    s->pct = -1;                                  /* no total known until said */
    s->elapsed_s = s->eta_s = -1; s->rate_bps = -1;   /* #410: unmeasured until said */
    char line[1024];
    while (fgets(line, sizeof line, fp)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!line[0] || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        const char *key = line;
        char *val = eq + 1, *f[12];
#define KEY(k) (strcmp(key, k) == 0)
        if (KEY("disk") && s->ndisks < MAX_DISKS) {
            split(val, f, 5);
            Disk *d = &s->disks[s->ndisks++];
            cp(d->dev, sizeof d->dev, f[0]); cp(d->model, sizeof d->model, f[1]);
            d->size_bytes = strtoull(f[2], NULL, 10);
            d->has_data = num(f[3], 0); d->removable = num(f[4], 0);
        } else if (KEY("folder") && s->nfolders < MAX_FOLDERS) {
            cp(s->folders[s->nfolders++], sizeof s->folders[0], val);
        } else if (KEY("image") && s->nimages < MAX_IMAGES) {
            split(val, f, 3);
            Image *im = &s->images[s->nimages++];
            cp(im->id, sizeof im->id, f[0]); cp(im->name, sizeof im->name, f[1]); cp(im->folder, sizeof im->folder, f[2]);
        } else if (KEY("machine") && s->nmachines < MAX_MACHINES) {
            split(val, f, 9);
            Machine *m = &s->machines[s->nmachines++];
            cp(m->name, sizeof m->name, f[0]); cp(m->mac, sizeof m->mac, f[1]);
            m->awake = num(f[2], 0); m->joined = num(f[3], 0); m->fresh_drawers = num(f[4], 0);
            cp(m->state, sizeof m->state, f[5]);
            m->pct = num(f[6], -1); m->moving = num(f[7], 0);
            cp(m->error, sizeof m->error, f[8]);
        } else if (KEY("machine_drawers")) {
            /* #695: machine-index|drawer_count (separate record so the 9-field
             * machine= format stays compatible). */
            if (split(val, f, 2) < 2) continue;
            unsigned long long mi, count;
            if (!parse_uint(f[0], &mi) || !parse_uint(f[1], &count)) continue;
            Machine *m = machine_at(s, mi);
            if (!m) continue;
            if (count < 1) count = 1;
            if (count > MAX_DRAWERS) count = MAX_DRAWERS;
            m->drawer_count = (int)count;
        } else if (KEY("room_drawer")) {
            /* #695: machine-index|port-or-0|serial|model|size|smart|fresh|selected */
            if (split(val, f, 8) < 8) continue;
            unsigned long long mi, port, size;
            if (!parse_uint(f[0], &mi) ||
                !parse_uint(f[1], &port) || port > MAX_DRAWERS ||
                !parse_uint(f[4], &size))
                continue;
            Machine *m = machine_at(s, mi);
            if (!m || m->nroom_drawers >= MAX_DRAWERS) continue;
            if (m->drawer_count < 1) m->drawer_count = MAX_DRAWERS;
            int actual_port = (int)port;
            if (actual_port == 0) actual_port = room_fallback_port(m);
            if (actual_port < 1 || actual_port > m->drawer_count) continue;
            RoomDrawer *d = &m->room_drawers[m->nroom_drawers++];
            d->port = actual_port;
            d->present = 1;
            cp(d->serial, sizeof d->serial, f[2]);
            cp(d->model, sizeof d->model, f[3]);
            d->size_bytes = size;
            cp(d->smart, sizeof d->smart, f[5]);
            d->fresh = num(f[6], 0) != 0;
            d->selected = num(f[7], 0) != 0;
        } else if (KEY("drawer") && s->ndrawers < MAX_DRAWERS) {
            int nf = split(val, f, 8);
            if (nf < 5) continue;                     /* malformed -- no phantom drawer */
            unsigned long long port, bytes, total;
            if (!parse_uint(f[0], &port) || port > INT_MAX) continue;  /* reject, don't fold to 0 */
            if (!parse_uint(f[3], &bytes)) continue;
            if (!parse_uint(f[4], &total)) continue;
            Drawer *d = &s->drawers[s->ndrawers++];
            d->port = (int)port;                      /* 0 = no SATA slot derived */
            cp(d->dev, sizeof d->dev, f[1]);
            cp(d->state, sizeof d->state, f[2]);
            d->bytes = bytes;
            d->total = total;
            cp(d->error, sizeof d->error, f[5]);
            /* #410: fields 7-8 are optional (an older agent sends six);
             * -1 / absent = not measured. */
            d->rate_bps = nf >= 8 && f[6][0] ? strtoll(f[6], NULL, 10) : -1;
            d->eta_s = nf >= 8 ? num(f[7], -1) : -1;
        } else if (KEY("cdisk") && s->nidisks < MAX_DRAWERS) {
            /* cdisk=port|dev|model|size|smart|cause -- the cloner's idle
             * inventory. NOT "disk=" -- that key already holds the build
             * machine's local disk list (s->disks) and would swallow these
             * first (#688). The 5th field (#709) and the 6th (#874: the
             * server's cause for failed_last) are optional so an older agent
             * still parses. */
            int nf = split(val, f, 6);
            if (nf < 4) continue;
            unsigned long long port, size;
            if (!parse_uint(f[0], &port) || port > INT_MAX) continue;
            if (!parse_uint(f[3], &size)) continue;
            IdleDisk *id = &s->idisks[s->nidisks++];
            id->port = (int)port;
            cp(id->model, sizeof id->model, f[2]);
            id->size = size;
            /* Only warn/fail/failed_last read as unhealthy; anything else --
             * passed, an absent field, an unknown string -- is treated as
             * unchecked. failed_last (#867/#874): the server remembers the
             * disk (serial) or the slot (machine+port) failed the previous
             * clone; the 6th field says why, as the server classified it. */
            const char *sm = (nf >= 5 && f[4][0]) ? f[4] : "unchecked";
            if (strcmp(sm, "passed") && strcmp(sm, "warn") && strcmp(sm, "fail")
                && strcmp(sm, "failed_last"))
                sm = "unchecked";
            cp(id->smart, sizeof id->smart, sm);
            const char *cause = (nf >= 6 && !strcmp(sm, "failed_last")) ? f[5] : "";
            if (strcmp(cause, "cable") && strcmp(cause, "disk")) cause = "";
            cp(id->cause, sizeof id->cause, cause);
        } else if (KEY("cloner")) {
            cp(s->cloner_image, sizeof s->cloner_image, val);
        } else if (KEY("smart_prompt")) {
            if (split(val, f, 4) < 4) continue;       /* nonce|port|verdict|reason */
            unsigned long long port;
            if (!parse_uint(f[1], &port) || port > INT_MAX) continue;
            s->smart_pending = 1;
            cp(s->smart_nonce, sizeof s->smart_nonce, f[0]);   /* string, echoed back */
            s->smart_port = (int)port;
            cp(s->smart_verdict, sizeof s->smart_verdict, f[2]);
            cp(s->smart_reason, sizeof s->smart_reason, f[3]);
        } else if (KEY("class") && s->nclasses < MAX_CLASSES) {
            split(val, f, 3);
            ClassGroup *g = &s->classes[s->nclasses++];
            cp(g->id, sizeof g->id, f[0]); cp(g->label, sizeof g->label, f[1]); g->machines = num(f[2], 0);
        } else if (KEY("task")) {
            s->task = !strcmp(val, "pending") ? TASK_PENDING : !strcmp(val, "running") ? TASK_RUNNING
                    : !strcmp(val, "done")    ? TASK_DONE    : !strcmp(val, "failed")  ? TASK_FAILED : TASK_NONE;
        } else if (KEY("task_direct")) s->task_direct = num(val, 0);   /* #715 */
        else if (KEY("task_name"))  cp(s->task_name, sizeof s->task_name, val);
        else if (KEY("task_disk"))    cp(s->task_disk, sizeof s->task_disk, val);
        else if (KEY("task_error"))   cp(s->task_error, sizeof s->task_error, val);
        else if (KEY("pct"))          s->pct = num(val, -1);
        else if (KEY("moving"))       s->moving = num(val, 0);
        else if (KEY("menu_class"))   s->menu_class = num(val, 0) != 0;
        else if (KEY("menu_tools"))   s->menu_tools = num(val, 0) != 0;   /* v1: off */
        else if (KEY("machine_name")) cp(s->machine_name, sizeof s->machine_name, val);   /* #1073 */
        else if (KEY("partition"))    s->partition = num(val, 0);
        else if (KEY("bytes"))        s->bytes = strtoull(val, NULL, 10);
        else if (KEY("title"))        cp(s->title, sizeof s->title, val);
        else if (KEY("sub"))          cp(s->sub, sizeof s->sub, val);
        else if (KEY("message")) {
            split(val, f, 2);
            cp(s->msg_title, sizeof s->msg_title, f[0]); cp(s->msg_sub, sizeof s->msg_sub, f[1]);
        } else if (KEY("round")) {
            split(val, f, 10);
            s->has_round = 1;
            cp(s->round_image, sizeof s->round_image, f[0]);
            s->wave_number = num(f[1], 0); s->wave_open = num(f[2], 0);
            s->written = num(f[3], 0); s->target = num(f[4], 0);
            s->ready = num(f[5], 0); s->remaining = num(f[6], 0);
            /* #410: an older agent sends seven fields -> "" -> -1 (unmeasured) */
            s->elapsed_s = num(f[7], -1);
            s->rate_bps = f[8][0] ? strtoll(f[8], NULL, 10) : -1;
            s->eta_s = num(f[9], -1);
        } else if (KEY("updated")) {
            s->updated = strtoll(val, NULL, 10);                 /* #410 */
        } else if (KEY("session")) {
            split(val, f, 7);
            s->has_session = 1;
            cp(s->sess_image, sizeof s->sess_image, f[0]); cp(s->sess_prefix, sizeof s->sess_prefix, f[1]);
            cp(s->sess_group, sizeof s->sess_group, f[2]);
            s->sess_open = num(f[3], 0); s->joined = num(f[4], 0);
            s->expected = num(f[5], 0); s->starts_in = num(f[6], 0);
        } else if (KEY("form_error"))  cp(s->form_error, sizeof s->form_error, val);
        else if (KEY("room_error"))    cp(s->room_error, sizeof s->room_error, val);
        else if (KEY("class_error"))   cp(s->class_error, sizeof s->class_error, val);
        else if (KEY("toast"))         cp(s->toast, sizeof s->toast, val);
        else fprintf(stderr, "native-gui: state: unknown key '%s' ignored\n", key);
#undef KEY
    }
}

int state_poll(StateFile *f, State *s) {
    struct stat st;
    if (stat(f->path, &st) != 0) {
        if (!f->complained) { perror(f->path); f->complained = 1; }
        return -1;
    }
    f->complained = 0;
    if (f->loaded && st.st_mtime == f->mtime && st.st_size == f->size) return 0;
    FILE *fp = fopen(f->path, "r");
    if (!fp) { perror(f->path); return -1; }
    state_parse(s, fp);
    fclose(fp);
    f->mtime = st.st_mtime; f->size = st.st_size; f->loaded = 1;
    return 1;
}
