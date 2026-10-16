#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include "state.h"

int lc_state_line(State *s, const char *key, char *val);   /* #1322, lc_state.c */

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

static void apply_tz(const char *tz) {
    static char applied[65];
    const char *value = tz[0] ? tz : "UTC0";
    if (!strcmp(applied, value)) return;
#ifdef _WIN32
    if (_putenv_s("TZ", value) != 0) return;
    _tzset();
#else
    if (setenv("TZ", value, 1) != 0) return;
    tzset();
#endif
    cp(applied, sizeof applied, value);
}

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
    s->hello_age = -1;
    s->hello_rc = -1;
    s->driver_models_read = -1;                   /* #1269: not read until said */
    int reported_long = 0, reported_disks = 0, reported_machines = 0;
    /* #928: the shrink question is all-or-nothing -- the declared count and
     * a bad row are remembered here and settled after the last line. */
    unsigned long long shrink_declared = 0; int shrink_seen = 0, shrink_bad = 0;
    /* 11/10: the agent's question dialog is all-or-nothing the same way. */
    unsigned long long ask_declared = 0; int ask_seen = 0, ask_bad = 0;
    char line[4096];
    while (fgets(line, sizeof line, fp)) {
        size_t len = strlen(line);
        if (len == 0 || line[len - 1] != '\n') {
            /* Reject the whole physical line. Keeping the prefix can end in
             * half a UTF-8 character, and the remainder would otherwise be
             * parsed as a second bogus key (R44 finding 1). A final line with
             * no newline is rejected by the same complete-record rule. */
            int ch;
            while ((ch = fgetc(fp)) != EOF && ch != '\n') {}
            s->rejected_lines++;
            if (!reported_long) {
                fprintf(stderr, "native-gui: state: unterminated or overlong line rejected\n");
                reported_long = 1;
            }
            continue;
        }
        line[--len] = 0;
        if (len && line[len - 1] == '\r') line[len - 1] = 0;
        if (!line[0] || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        const char *key = line;
        char *val = eq + 1, *f[16];   /* #1366: room_drawer= has 13 fields */
        if (lc_state_line(s, key, val)) continue;   /* #1322: lc_* records */
#define KEY(k) (strcmp(key, k) == 0)
        if (KEY("disk")) {
            if (s->ndisks >= MAX_DISKS) {
                s->hidden_disks++;
                if (!reported_disks) {
                    fprintf(stderr, "native-gui: state: too many disks (max %d); extra rows hidden\n", MAX_DISKS);
                    reported_disks = 1;
                }
                continue;
            }
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
        } else if (KEY("machine")) {
            if (s->nmachines >= MAX_MACHINES) {
                s->hidden_machines++;
                if (!reported_machines) {
                    fprintf(stderr, "native-gui: state: too many machines (max %d); extra rows hidden\n", MAX_MACHINES);
                    reported_machines = 1;
                }
                continue;
            }
            split(val, f, 10);
            Machine *m = &s->machines[s->nmachines++];
            cp(m->name, sizeof m->name, f[0]); cp(m->mac, sizeof m->mac, f[1]);
            m->awake = num(f[2], 0); m->joined = num(f[3], 0); m->fresh_drawers = num(f[4], 0);
            cp(m->state, sizeof m->state, f[5]);
            m->pct = num(f[6], -1); m->moving = num(f[7], 0);
            cp(m->error, sizeof m->error, f[8]);
            m->local_copy = !strcmp(f[9], "1");
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
            /* #695: machine-index|port-or-0|serial|model|size|smart|fresh|selected
             * #1269: |drivers-state|drivers-detail -- optional (an older agent sends eight)
             * #704: |slot-name -- optional, "" = "דיסק N"
             * #432: |link-state -- optional; only "slow" colours the slot
             * #1366: |crc -- optional; the count when over the threshold, "" = not */
            if (split(val, f, 13) < 8) continue;
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
            /* #1235: -1 = no serial (#105, fresh null): "cannot identify",
             * which is neither ready nor written. */
            d->fresh = num(f[6], 0) == 1;
            d->unidentified = num(f[6], 0) < 0;
            d->selected = num(f[7], 0) != 0;
            /* #1269: only the three words the server keeps (well_formed_drivers);
             * anything else is "did not run", never drawn as staged. */
            if (!strcmp(f[8], "staged") || !strcmp(f[8], "skipped") || !strcmp(f[8], "failed")) {
                cp(d->drv_state, sizeof d->drv_state, f[8]);
                cp(d->drv_detail, sizeof d->drv_detail, f[9]);
            }
            /* #704: the drawer's own name (by its slot; NVMe by serial) wins
             * over a room_slot_name= for the same port, in any order. */
            if (f[10][0]) cp(m->slot_names[actual_port - 1], sizeof m->slot_names[0], f[10]);
            /* A port-0 drawer only borrows the slot it is drawn in: that SATA
             * slot's name is not its name. */
            else if (port == 0) m->slot_names[actual_port - 1][0] = 0;
            d->fallback = port == 0;
            d->link_slow = !strcmp(f[11], "slow");
            unsigned long long crc;
            d->crc_suspect = parse_uint(f[12], &crc) && crc > 0 && crc < 100000000ULL ? (long)crc : 0;
        } else if (KEY("room_warn")) {
            /* #432: one line of round.precheck; an empty one is not a warning */
            if (!val[0]) continue;
            if (s->nroom_warn >= MAX_ROOM_NOTES) { s->hidden_room_warn++; continue; }
            cp(s->room_warn[s->nroom_warn++], sizeof s->room_warn[0], val);
        } else if (KEY("room_label")) {
            cp(s->room_label, sizeof s->room_label, val);
        } else if (KEY("room_missing")) {
            /* #65/#1367: mac|name|reason|can_poweroff|text|local_copy.
             * Five fields remain valid for an older agent and mean no copy. */
            int nf = split(val, f, 6);
            if ((nf != 5 && nf != 6) || !f[0][0] || !f[4][0]) continue;
            if (s->nroom_missing >= MAX_ROOM_NOTES) { s->hidden_room_missing++; continue; }
            int i = s->nroom_missing++;
            cp(s->room_missing[i].mac, sizeof s->room_missing[i].mac, f[0]);
            cp(s->room_missing[i].name, sizeof s->room_missing[i].name, f[1]);
            cp(s->room_missing[i].reason, sizeof s->room_missing[i].reason, f[2]);
            s->room_missing[i].can_poweroff = !strcmp(f[3], "1");
            cp(s->room_missing[i].text, sizeof s->room_missing[i].text, f[4]);
            s->room_missing[i].local_copy = nf == 6 && !strcmp(f[5], "1");
        } else if (KEY("room_slot_name")) {
            /* #704: machine-index|port|name -- a SATA slot's name, empty slots too */
            if (split(val, f, 3) != 3 || !f[2][0]) continue;
            unsigned long long mi, port;
            if (!parse_uint(f[0], &mi) || !parse_uint(f[1], &port) ||
                port < 1 || port > MAX_DRAWERS)
                continue;
            Machine *m = machine_at(s, mi);
            if (!m || m->slot_names[port - 1][0]) continue;
            int borrowed = 0;                  /* a port-0 drawer drawn in this slot */
            for (int i = 0; i < m->nroom_drawers; i++)
                if (m->room_drawers[i].fallback && m->room_drawers[i].port == (int)port) borrowed = 1;
            if (borrowed) continue;
            cp(m->slot_names[port - 1], sizeof m->slot_names[0], f[2]);
        } else if (KEY("cslot_name")) {
            /* #704: port|name -- the cloner's own slot names (slotnames.sh) */
            if (split(val, f, 2) != 2 || !f[1][0]) continue;
            unsigned long long port;
            if (!parse_uint(f[0], &port) || port < 1 || port > MAX_DRAWERS) continue;
            cp(s->cslot_names[port - 1], sizeof s->cslot_names[0], f[1]);
        } else if (KEY("driver_models")) {
            /* #1269: how many models the bridge read; -1 = it could not read them */
            if (!strcmp(val, "-1")) { s->driver_models_read = -1; continue; }
            unsigned long long n;
            if (parse_uint(val, &n) && n <= INT_MAX) s->driver_models_read = (int)n;
        } else if (KEY("driver_model")) {
            /* #1269: vendor|model -- both needed, or the server could not match it */
            if (split(val, f, 3) != 2 || !f[0][0] || !f[1][0]) continue;
            if (s->ndriver_models >= MAX_DRIVER_MODELS) { s->hidden_models++; continue; }
            DriverModel *dm = &s->driver_models[s->ndriver_models++];
            cp(dm->vendor, sizeof dm->vendor, f[0]);
            cp(dm->model, sizeof dm->model, f[1]);
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
            int nf = split(val, f, 9);   /* #1366: field 9, the CRC count over the threshold */
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
            /* #1366: on fail the 6th field is the SMART reason of the F1 stop */
            const char *cause = (nf >= 6 && (!strcmp(sm, "failed_last") || !strcmp(sm, "fail"))) ? f[5] : "";
            if (!strcmp(sm, "failed_last") ? strcmp(cause, "cable") && strcmp(cause, "disk")
                    : strcmp(cause, "health_failed") && strcmp(cause, "pending_sectors") && strcmp(cause, "uncorrectable"))
                cause = "";
            cp(id->cause, sizeof id->cause, cause);
            /* #432: fields 7/8 (link, port max), "6.0"-shaped or not read --
             * "null", an older agent's absent field and junk all mean unknown */
            for (int k = 0; k < 2; k++) {
                const char *v = nf >= 7 + k ? f[6 + k] : "";
                int ok = strlen(v) == 3 && v[0] >= '0' && v[0] <= '9' && v[1] == '.' && v[2] >= '0' && v[2] <= '9';
                cp(k ? id->link_max : id->link, sizeof id->link, ok ? v : "");
            }
            unsigned long long crc;
            id->crc_suspect = nf >= 9 && parse_uint(f[8], &crc) && crc > 0 && crc < 100000000ULL ? (long)crc : 0;
        } else if (KEY("disk_probe")) {
            if (!strcmp(val, "drives") || !strcmp(val, "no_disks") ||
                !strcmp(val, "no_ports") || !strcmp(val, "unchecked"))
                cp(s->disk_probe, sizeof s->disk_probe, val);
            else
                cp(s->disk_probe, sizeof s->disk_probe, "unchecked");
        } else if (KEY("cloner")) {
            cp(s->cloner_image, sizeof s->cloner_image, val);
        } else if (KEY("server_restarted_wave")) {
            s->server_restarted_wave = !strcmp(val, "1");
        } else if (KEY("smart_prompt")) {
            if (split(val, f, 4) < 4) continue;       /* nonce|port|verdict|reason */
            unsigned long long port;
            if (!parse_uint(f[1], &port) || port > INT_MAX) continue;
            s->smart_pending = 1;
            cp(s->smart_nonce, sizeof s->smart_nonce, f[0]);   /* string, echoed back */
            s->smart_port = (int)port;
            cp(s->smart_verdict, sizeof s->smart_verdict, f[2]);
            cp(s->smart_reason, sizeof s->smart_reason, f[3]);
        } else if (KEY("shrink_prompt")) {
            /* nonce|count. The nonce is echoed back on the answer, so it is
             * kept only in the shape the bridge accepts: digits and '-'. */
            unsigned long long n;
            if (shrink_seen++ || split(val, f, 3) != 2 || !f[0][0] ||
                strspn(f[0], "0123456789-") != strlen(f[0]) ||
                strlen(f[0]) >= sizeof s->shrink_nonce ||
                !parse_uint(f[1], &n) || n < 1 || n > MAX_SHRINK_PARTS) { shrink_bad = 1; continue; }
            cp(s->shrink_nonce, sizeof s->shrink_nonce, f[0]);
            shrink_declared = n;
        } else if (KEY("shrink_partition")) {
            /* idx|old bytes|new bytes -- a shrink makes a partition smaller */
            unsigned long long idx, old_b, new_b;
            if (split(val, f, 4) != 3 || !parse_uint(f[0], &idx) || idx < 1 || idx > INT_MAX ||
                !parse_uint(f[1], &old_b) || !parse_uint(f[2], &new_b) ||
                new_b < 1 || new_b >= old_b || s->nshrink >= MAX_SHRINK_PARTS) { shrink_bad = 1; continue; }
            s->shrink[s->nshrink].idx = (int)idx;
            s->shrink[s->nshrink].old_bytes = old_b;
            s->shrink[s->nshrink].new_bytes = new_b;
            s->nshrink++;
        } else if (KEY("ask_prompt")) {
            /* nonce|kind|count|subject (11/10). The nonce is echoed back, so
             * only digits and '-'; the subject (a disk, or seconds) is drawn,
             * so only [A-Za-z0-9._/-]. growback has 1..MAX_ASK_ROWS rows,
             * capture-end none. */
            unsigned long long n;
            int kind = ASK_NONE;
            if (ask_seen++ || split(val, f, 5) != 4) { ask_bad = 1; continue; }
            if (!strcmp(f[1], "growback")) kind = ASK_GROWBACK;
            else if (!strcmp(f[1], "capture-end")) kind = ASK_CAPTURE_END;
            if (kind == ASK_NONE || !f[0][0] ||
                strspn(f[0], "0123456789-") != strlen(f[0]) ||
                strlen(f[0]) >= sizeof s->ask_nonce ||
                strspn(f[3], "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-") != strlen(f[3]) ||
                strlen(f[3]) >= sizeof s->ask_subject ||
                !parse_uint(f[2], &n) || n > MAX_ASK_ROWS ||
                (kind == ASK_GROWBACK && n < 1) || (kind == ASK_CAPTURE_END && n != 0)) { ask_bad = 1; continue; }
            cp(s->ask_nonce, sizeof s->ask_nonce, f[0]);
            cp(s->ask_subject, sizeof s->ask_subject, f[3]);
            s->ask_kind = kind;
            ask_declared = n;
        } else if (KEY("ask_row")) {
            /* idx|now bytes|orig bytes|table|fs -- the grow-back makes a
             * partition (or its filesystem) bigger again: now < orig */
            unsigned long long idx, now_b, orig_b;
            if (split(val, f, 5) != 4 || !parse_uint(f[0], &idx) || idx < 1 || idx > INT_MAX ||
                !parse_uint(f[1], &now_b) || !parse_uint(f[2], &orig_b) ||
                now_b < 1 || now_b >= orig_b || s->nask >= MAX_ASK_ROWS ||
                (strcmp(f[3], "table") && strcmp(f[3], "fs"))) { ask_bad = 1; continue; }
            s->ask[s->nask].idx = (int)idx;
            s->ask[s->nask].now_bytes = now_b;
            s->ask[s->nask].orig_bytes = orig_b;
            s->ask[s->nask].fs_only = !strcmp(f[3], "fs");
            s->nask++;
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
        else if (KEY("agent_version")) cp(s->agent_version, sizeof s->agent_version, val);
        else if (KEY("pct"))          s->pct = num(val, -1);
        else if (KEY("moving"))       s->moving = num(val, 0);
        else if (KEY("menu_class"))   s->menu_class = num(val, 0) != 0;
        else if (KEY("menu_tools"))   s->menu_tools = num(val, 0) != 0;   /* v1: off */
        else if (KEY("machine_name")) cp(s->machine_name, sizeof s->machine_name, val);   /* #1073 */
        else if (KEY("server_name")) cp(s->server_name, sizeof s->server_name, val);         /* #1252 */
        else if (KEY("server_addr")) cp(s->server_addr, sizeof s->server_addr, val);         /* #1252 */
        else if (KEY("server_version")) cp(s->server_version, sizeof s->server_version, val); /* #1252 */
        else if (KEY("tz")) {
            size_t n = strlen(val);
            if (n >= 1 && n <= 64 && strspn(val, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789<>+-,./:") == n)
                cp(s->tz, sizeof s->tz, val);
        }
        else if (KEY("hello_age")) {
            unsigned long long v;
            if (parse_uint(val, &v) && v <= INT_MAX) s->hello_age = (int)v;
        }
        else if (KEY("hello_rc")) {
            unsigned long long v;
            if (parse_uint(val, &v) && v <= 999) s->hello_rc = (int)v;
        }
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
        } else if (KEY("idle_poweroff_at")) {
            s->idle_poweroff_at = strtoll(val, NULL, 10);        /* #434 */
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
    s->shrink_pending = shrink_seen && !shrink_bad && shrink_declared > 0 &&
                        (unsigned long long)s->nshrink == shrink_declared;
    int shrink_broken = (shrink_seen || s->nshrink) && !s->shrink_pending;
    if (shrink_broken) {
        fprintf(stderr, "native-gui: state: shrink question incomplete or malformed; not shown\n");
        s->nshrink = 0; s->shrink_nonce[0] = 0;
    }
    s->ask_pending = ask_seen && !ask_bad && s->ask_kind != ASK_NONE &&
                     (unsigned long long)s->nask == ask_declared;
    int ask_broken = (ask_seen || s->nask) && !s->ask_pending;
    if (ask_broken) {
        fprintf(stderr, "native-gui: state: question incomplete or malformed; not shown\n");
        s->nask = 0; s->ask_nonce[0] = 0; s->ask_subject[0] = 0; s->ask_kind = ASK_NONE;
    }
    int hidden = s->hidden_disks + s->hidden_machines + s->hidden_models;   /* #1269 */
    if (s->rejected_lines && hidden)
        snprintf(s->state_warning, sizeof s->state_warning,
                 "קובץ המצב חלקי: %d שורות נדחו · +%d נוספים, לא מוצגים",
                 s->rejected_lines, hidden);
    else if (s->rejected_lines)
        snprintf(s->state_warning, sizeof s->state_warning,
                 "קובץ המצב חלקי: %d שורות ארוכות או לא שלמות נדחו", s->rejected_lines);
    else if (hidden)
        snprintf(s->state_warning, sizeof s->state_warning,
                 "+%d נוספים, לא מוצגים", hidden);
    /* #928: a question that cannot be drawn whole is said, not hidden --
     * the agent cancels the capture when no answer comes. */
    if (shrink_broken && !s->state_warning[0])
        snprintf(s->state_warning, sizeof s->state_warning,
                 "שאלת הכיווץ הגיעה פגומה ולא מוצגת — הקליטה תבוטל");
    /* 11/10: the same for the question dialog -- no answer is the safe one */
    if (ask_broken && !s->state_warning[0])
        snprintf(s->state_warning, sizeof s->state_warning,
                 "שאלה מהסוכן הגיעה פגומה ולא מוצגת — בלי תשובה נבחרת ברירת המחדל הבטוחה");
}

int state_poll(StateFile *f, State *s) {
    struct stat st;
    if (stat(f->path, &st) != 0) {
        if (!f->complained) { perror(f->path); f->complained = 1; }
        return -1;
    }
    f->complained = 0;
    time_t now = time(NULL);
    int stale = now != (time_t)-1 && now > st.st_mtime && now - st.st_mtime > 10;
    if (f->loaded && st.st_mtime == f->mtime && st.st_size == f->size) {
        if (s->stale != stale) { s->stale = stale; return 1; }
        return 0;
    }
    FILE *fp = fopen(f->path, "r");
    if (!fp) { perror(f->path); return -1; }
    State next;
    state_parse(&next, fp);
    fclose(fp);
    next.stale = stale;
    *s = next;
    apply_tz(s->tz);
    f->mtime = st.st_mtime; f->size = st.st_size; f->loaded = 1;
    return 1;
}
