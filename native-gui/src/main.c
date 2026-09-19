/* imagectl-station-gui -- the build machine's screens, drawn natively
 * (Pango/Cairo -> KMS), replacing cage+chromium for the cards of
 * server/static/station/index.html.
 *
 * Contract with the agent (POSIX sh) -- README "Contract with the agent":
 *   --auth-cmd CMD   login: CMD gets "<user>\n<pass>\n" on stdin; exit 0
 *                    means signed in, and its first stdout line is the
 *                    role ("admin" shows the capture card, anything else
 *                    hides it, like station.js afterLogin).
 *   --state FILE     key=value lines the agent rewrites (disks, machines,
 *                    the running task...); re-read every 2 s when changed.
 *   stdout           one record per operator action: a token line
 *                    (capture | restore | room | classes | back | again |
 *                    capture-start | room-open | room-wake | room-start |
 *                    room-close | class-pick | class-start | class-close |
 *                    smart-replace | smart-rescue | smart-skip |
 *                    tool-list | tool-run|<id>|<arg>|<confirm>  (#649)),
 *                    then key=value detail lines, then an empty line.
 *   exit 0           after "restore" (the agent takes the disk);
 *   exit 1           could not start / render, or stopped by a signal.
 * Without --auth-cmd every login fails (closed by default); --demo accepts
 * any non-empty user name, for looking at the screens only. */
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/wait.h>
#include "backend.h"
#include "input.h"
#include "state.h"
#include "text.h"
#include "ui.h"

static volatile sig_atomic_t stop_now = 0;
static void on_signal(int sig) { (void)sig; stop_now = 1; }

static const char *USAGE =
"usage: imagectl-station-gui [--mac AA:BB:CC:DD:EE:FF] [--ip 10.0.0.5] [--title T]\n"
"                            [--theme light|dark] [--backend auto|drm|fbdev|mem] [--fb-file PATH]\n"
"                            [--auth-cmd CMD | --demo] [--state FILE] [--no-input]\n"
"                            [--screen progress|class|cloner] [--signed-in USER [--role admin|deploy]]\n"
"       imagectl-station-gui --png PREFIX [--size WxH] [--state FILE] [--mac ..] [--ip ..] [--user NAME]\n"
"  --png renders every card, both themes, to PREFIX-<card>-{light,dark}.png and exits\n"
"  (no display needed). Without --state it uses built-in sample data.\n"
"  --backend mem draws into --fb-file (default /run/imagectl/fb.mem) for the remote\n"
"  monitor when the machine has no display (#835); auto falls back to it last.\n";

/* ---- login through the agent's command ------------------------------------- */

static int run_auth(const char *cmd, const char *user, const char *pass, char *role, size_t n) {
    int in_p[2], out_p[2];
    if (pipe(in_p) || pipe(out_p)) return -1;
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (pid == 0) {
        dup2(in_p[0], 0); dup2(out_p[1], 1);
        close(in_p[0]); close(in_p[1]); close(out_p[0]); close(out_p[1]);
        execl("/bin/sh", "sh", "-c", cmd, (char *)NULL);
        _exit(127);
    }
    close(in_p[0]); close(out_p[1]);
    FILE *w = fdopen(in_p[1], "w");
    if (w) { fprintf(w, "%s\n%s\n", user, pass); fclose(w); } else close(in_p[1]);
    char buf[128] = "";
    ssize_t r = read(out_p[0], buf, sizeof buf - 1);
    close(out_p[0]);
    if (r > 0) { buf[r] = 0; buf[strcspn(buf, "\r\n")] = 0; }
    int status = 0;
    waitpid(pid, &status, 0);
    snprintf(role, n, "%s", buf);
    return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1;
}

static void do_login(App *a, const char *auth_cmd, int demo) {
    char role[64] = "";
    int ok;
    if (auth_cmd)     ok = run_auth(auth_cmd, a->user, a->pass, role, sizeof role) == 0;
    else if (demo)    { ok = a->user[0] != 0; snprintf(role, sizeof role, "admin"); }
    else {
        ok = 0;
        fprintf(stderr, "native-gui: login attempted but no --auth-cmd was given (and no --demo)\n");
    }
    if (!ok) {
        snprintf(a->error, sizeof a->error, "שם משתמש או סיסמה שגויים");   /* station.js */
        return;
    }
    a->error[0] = 0;
    snprintf(a->signed_user, sizeof a->signed_user, "%s", a->user);
    a->admin = strcmp(role, "admin") == 0;
    a->signed_in = 1;
    memset(a->pass, 0, sizeof a->pass);
    a->menu_focus = -1;
    a->mode = MODE_MENU;                            /* afterLogin: both roles start at the menu */
}

/* ---- stdout records --------------------------------------------------------- */

static void emit(const char *token) { printf("%s\n", token); }
static void emit_kv(const char *k, const char *v) { printf("%s=%s\n", k, v); }
static void emit_end(void) { printf("\n"); fflush(stdout); }
static void emit1(const char *token) { emit(token); emit_end(); }

/* A SMART choice echoes the nonce of the request it was drawn for, so the
 * bridge binds the click to that exact request. The port alone cannot: the
 * same disk can be prompted again (a new request, a new nonce), and a stale
 * click would still match the port. The nonce is unique per request. */
static void emit_smart(const App *a, const char *action) {
    char line[64];
    snprintf(line, sizeof line, "%s|%s", action, a->st.smart_nonce);
    emit1(line);
}

/* ---- small helpers ------------------------------------------------------------ */

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void toast(App *a, const char *text) {          /* station.js toast(): 2600 ms */
    snprintf(a->toast, sizeof a->toast, "%s", text);
    a->toast_until = now_s() + 2.6;
}

static void trim(char *s) {
    size_t n = strlen(s);
    while (n && (s[n - 1] == ' ' || s[n - 1] == '\t')) s[--n] = 0;
    size_t i = 0;
    while (s[i] == ' ' || s[i] == '\t') i++;
    if (i) memmove(s, s + i, n - i + 1);
}

static void field_append(char *buf, size_t n, int ch) {
    size_t len = strlen(buf);
    if (len + 1 < n) { buf[len] = (char)ch; buf[len + 1] = 0; }
}

static void field_backspace(char *buf) {
    size_t len = strlen(buf);
    while (len > 0) { len--; if ((buf[len] & 0xC0) != 0x80) break; }   /* whole UTF-8 char */
    buf[len] = 0;
}

/* The text buffer behind a field id, or NULL. */
static char *field_buf(App *a, int id, size_t *n) {
    switch (id) {
    case HIT_USER:          *n = sizeof a->user;          return a->user;
    case HIT_PASS:          *n = sizeof a->pass;          return a->pass;
    case HIT_NAME:          *n = sizeof a->name;          return a->name;
    case HIT_DESC:          *n = sizeof a->desc;          return a->desc;
    case HIT_FOLDER_NEW:    *n = sizeof a->folder_new;    return a->folder_new;
    case HIT_ROOM_TARGET:   *n = sizeof a->room_target;   return a->room_target;
    case HIT_ROOM_CONFIRM:  *n = sizeof a->room_confirm;  return a->room_confirm;
    case HIT_RESTORE_CONFIRM: *n = sizeof a->restore_confirm; return a->restore_confirm;
    case HIT_CLASS_CONFIRM: *n = sizeof a->class_confirm; return a->class_confirm;
    case HIT_TOOL_ARG:      *n = sizeof a->tool_arg;      return a->tool_arg;        /* #649 */
    case HIT_TOOL_CONFIRM:  *n = sizeof a->tool_confirm;  return a->tool_confirm;
    default: *n = 0; return NULL;
    }
}

/* The fields present on the current screen, in Tab order. */
static int fields_on(const App *a, int *out) {
    int n = 0;
    switch (a->screen) {
    case SCREEN_LOGIN: out[n++] = HIT_USER; out[n++] = HIT_PASS; break;
    case SCREEN_PICK:
        if (a->chosen_disk >= 0) {
            out[n++] = HIT_NAME; out[n++] = HIT_DESC;
            if (a->newfolder_shown) out[n++] = HIT_FOLDER_NEW;
        }
        break;
    case SCREEN_ROOM:
        if (!a->st.has_round) { if (a->mode != MODE_DIRECT) out[n++] = HIT_ROOM_TARGET; }   /* #715: no count field */
        else if (a->room_confirming) out[n++] = HIT_ROOM_CONFIRM;
        break;
    case SCREEN_CLASS:
        if (a->st.has_session && a->class_confirming) out[n++] = HIT_CLASS_CONFIRM;
        break;
    case SCREEN_RESTORE:
        out[n++] = HIT_RESTORE_CONFIRM;
        break;
    case SCREEN_TOOLS:                                   /* #649: the confirm view only */
        if (a->tools_view == TOOLS_CONFIRM && a->tool_sel >= 0 && a->tool_sel < a->ntools) {
            const Tool *t = &a->tools[a->tool_sel];
            if (t->args[0] && strncmp(t->args, "disk:", 5)) out[n++] = HIT_TOOL_ARG;
            if (tools_risk_level(t->risk)) out[n++] = HIT_TOOL_CONFIRM;
        }
        break;
    default: break;
    }
    return n;
}

static int field_visible(const App *a, int id) {
    int ids[8], n = fields_on(a, ids);
    for (int i = 0; i < n; i++) if (ids[i] == id) return 1;
    return 0;
}

/* Leaving a sub-screen for the menu (st-back-capture / room-back / cls-back). */
static void go_menu(App *a) {
    a->mode = MODE_MENU;
    a->chosen_disk = -1; a->dd_open = 0;
    a->newfolder_shown = 0;
    a->room_confirming = 0; a->room_target_set = 0;
    a->class_confirming = 0;
    a->menu_focus = -1;
    a->tools_view = TOOLS_LIST; a->tool_sel = -1;          /* #649 */
}

/* An action of a live classroom round without a session: classes.js
 * authed() -> 401 -> toast, and the live view stays (#34). */
static int class_gate(App *a) {
    if (a->signed_in) return 1;
    toast(a, "החיבור פג — נדרשת כניסה מחדש.");
    return 0;
}

/* ---- events --------------------------------------------------------------------- */

/* Same visibility rule as screens.c, in MENU order (#1073): room, direct,
 * restore for everyone; capture admin-only, last; the class card follows
 * the server's switch (#880, pinned off in v1). Returns how many. */
static int visible_card_ids(const App *a, int ids[5]) {
    int n = 0;
    ids[n++] = HIT_ROOM;
    ids[n++] = HIT_DIRECT;
    ids[n++] = HIT_RESTORE;
    if (a->admin) ids[n++] = HIT_CAPTURE;
    if (a->st.menu_class) ids[n++] = HIT_CLASSES;
    return n;
}

/* Returns 1 when the process should exit (after "restore"). */
static int choose_card(App *a, int id) {
    switch (id) {
    case HIT_CAPTURE: a->mode = MODE_CAPTURE; a->chosen_disk = -1; a->newfolder_shown = 0; emit1("capture"); break;
    case HIT_RESTORE: a->mode = MODE_RESTORE; a->restore_image_sel = 0; a->restore_confirm[0] = 0; emit1("restore"); break;
    case HIT_ROOM:    a->mode = MODE_ROOM; a->room_confirming = 0; a->room_target_set = 0; a->image_sel = 0; emit1("room"); break;
    case HIT_DIRECT:  a->mode = MODE_DIRECT; a->room_confirming = 0; a->room_target_set = 0; a->nroom_selection = 0; emit1("direct"); break;   /* #715 */
    case HIT_CLASSES: a->mode = MODE_CLASSES; a->class_confirming = 0; emit1("classes"); break;
    case HIT_TOOLS:                                       /* #649: ask the bridge for the list */
        a->mode = MODE_TOOLS; a->tools_view = TOOLS_LIST; a->tool_sel = -1;
        a->tool_error[0] = 0; a->tool_list_scroll = 0;
        emit1("tool-list");
        break;
    }
    return 0;
}

/* ---- #649: the toolbox ---------------------------------------------------------- */

/* Send tool-run|<id>|<arg>|<confirm>; the bridge (tools.sh) checks the name
 * again before any module runs -- this local check only saves a round trip.
 * '|' and newlines cannot travel in the record and are refused here. */
static void tool_run(App *a) {
    if (a->tool_sel < 0 || a->tool_sel >= a->ntools) return;
    const Tool *t = &a->tools[a->tool_sel];
    int level = tools_risk_level(t->risk), disk = !strncmp(t->args, "disk:", 5);
    char arg[96] = "", confirm[96];
    if (disk) {
        if (a->tool_disk_sel < 0 || a->tool_disk_sel >= a->st.ndisks) {
            snprintf(a->tool_error, sizeof a->tool_error, "בחרו דיסק"); return;
        }
        snprintf(arg, sizeof arg, "%s", a->st.disks[a->tool_disk_sel].dev);
    } else if (t->args[0]) {
        snprintf(arg, sizeof arg, "%s", a->tool_arg); trim(arg);
        if (!arg[0] || strpbrk(arg, "|\r\n ")) {
            snprintf(a->tool_error, sizeof a->tool_error, "מלאו את השדה: %s (בלי רווחים)", t->args); return;
        }
    }
    snprintf(confirm, sizeof confirm, "%s", a->tool_confirm); trim(confirm);
    if (level) {
        if (strpbrk(confirm, "|\r\n") || !confirm[0] ||
            (a->tools_machine[0] && strcmp(confirm, a->tools_machine))) {   /* principle 7 */
            snprintf(a->tool_error, sizeof a->tool_error, "הקלידו את שם המחשב בדיוק כפי שהוא רשום"); return;
        }
    } else confirm[0] = 0;
    a->tool_error[0] = 0;
    char line[400];
    snprintf(line, sizeof line, "tool-run|%s|%s|%s", t->id, arg, confirm);
    emit1(line);
    a->tool_running = 1; a->tool_rc = -1; a->tool_spin = 0;
    tool_output_set(a, "");
    a->tools_view = TOOLS_OUTPUT;
}

/* A row was clicked: ro without an argument runs at once; anything else
 * goes through the confirm view (argument and/or the machine name). */
static void tool_open(App *a, int i) {
    if (i < 0 || i >= a->ntools) return;
    a->tool_sel = i; a->tool_disk_sel = -1; a->tool_arg[0] = a->tool_confirm[0] = a->tool_error[0] = 0;
    const Tool *t = &a->tools[i];
    if (!tools_risk_level(t->risk) && !t->args[0]) { tool_run(a); return; }
    a->tools_view = TOOLS_CONFIRM;
    a->focus = t->args[0] && strncmp(t->args, "disk:", 5) ? HIT_TOOL_ARG : HIT_TOOL_CONFIRM;
}

static void start_capture(App *a) {
    char name[96]; snprintf(name, sizeof name, "%s", a->name); trim(name);
    if (a->chosen_disk < 0 || !name[0]) {
        snprintf(a->st.form_error, sizeof a->st.form_error, "בחרו כונן ותנו שם לאימג'");
        return;
    }
    char typed[64]; snprintf(typed, sizeof typed, "%s", a->newfolder_shown ? a->folder_new : ""); trim(typed);
    const char *folder = typed[0] ? typed : a->folder_sel > 0 && a->folder_sel <= a->st.nfolders ? a->st.folders[a->folder_sel - 1] : "";
    a->st.form_error[0] = 0;
    emit("capture-start");
    emit_kv("dev", a->st.disks[a->chosen_disk].dev);
    emit_kv("name", a->name);
    emit_kv("desc", a->desc);
    emit_kv("folder", folder);
    emit_kv("folder_new", typed[0] ? "yes" : "no");     /* resolveFolder: a typed one is created first */
    emit_end();
}

/* #706: write a library image onto THIS machine's disk. The agent rediscovers
 * the internal disk before writing -- the GUI never supplies a device. */
static void start_restore(App *a) {
    if (a->restore_image_sel <= 0 || a->restore_image_sel > a->st.nimages) {
        snprintf(a->st.form_error, sizeof a->st.form_error, "בחרו אימג' לשחזור");
        return;
    }
    /* principle 7, #1073: the typed confirmation is this machine's registered
     * name (state machine_name=). No name -> nothing to confirm against -> refuse;
     * the bridge re-reads the name from the server before it hands off. */
    if (!a->st.machine_name[0]) {
        snprintf(a->st.form_error, sizeof a->st.form_error, "למחשב הזה אין שם במרשם השרת — לא ניתן לאשר");
        return;
    }
    if (strcmp(a->restore_confirm, a->st.machine_name) != 0) {
        snprintf(a->st.form_error, sizeof a->st.form_error, "הקלידו את שם המחשב (%s) לאישור מחיקת הדיסק", a->st.machine_name);
        return;
    }
    a->st.form_error[0] = 0;
    emit("restore-start");
    emit_kv("image", a->st.images[a->restore_image_sel - 1].id);
    emit_kv("confirm", a->restore_confirm);
    emit_end();
}

static int room_effective_selected(const App *a, const Machine *m, const RoomDrawer *d);

/* #715: the chosen drawers, as the bridge's record value:
 * "<mac>@<port>,<port>/<mac>@<port>". Only present, fresh, awake drawers can
 * be selected (room_toggle_selected), so what is here is what the server
 * validates as target_slots. Returns the number of drawers, 0 when none. */
static int room_selection_encode(const App *a, char *out, size_t n) {
    size_t used = 0; int drawers = 0;
    out[0] = 0;
    for (int mi = 0; mi < a->st.nmachines; mi++) {
        const Machine *m = &a->st.machines[mi];
        int first = 1;
        for (int i = 0; i < m->nroom_drawers; i++) {
            const RoomDrawer *d = &m->room_drawers[i];
            if (!d->present || d->port < 1) continue;
            if (!room_effective_selected(a, m, d)) continue;
            int k = snprintf(out + used, n - used, "%s%s%s%d",
                             first ? (drawers ? "/" : "") : ",",
                             first ? m->mac : "", first ? "@" : "", d->port);
            if (k < 0 || (size_t)k >= n - used) return 0;   /* truncated = no order */
            used += (size_t)k; first = 0; drawers++;
        }
    }
    return drawers;
}

static void open_direct(App *a) {
    char slots[MAX_ROOM_SELECTIONS * 8 + MAX_MACHINES * 24];
    int drawers = room_selection_encode(a, slots, sizeof slots);
    if (!drawers) {
        snprintf(a->st.room_error, sizeof a->st.room_error, "בחרו לפחות מגירה אחת במחשב ער");
        return;
    }
    a->st.room_error[0] = 0;
    emit("direct-open");
    emit_kv("slots", slots);
    emit_end();
}

static void open_round(App *a) {
    if (a->mode == MODE_DIRECT) { open_direct(a); return; }   /* #715: no image, the disk */
    int target = atoi(a->room_target);
    if (a->image_sel <= 0 || a->image_sel > a->st.nimages || target <= 0) {
        snprintf(a->st.room_error, sizeof a->st.room_error, "בחרו אימג' וקבעו יעד כוננים");
        return;
    }
    a->st.room_error[0] = 0;
    emit("room-open");
    emit_kv("image", a->st.images[a->image_sel - 1].id);
    emit_kv("target", a->room_target);
    emit_end();
}

/* Stop behind the typed image name (principle 7); the server decides (#533/#581). */
static void close_round(App *a, int *confirming, char *typed, int focus_id, const char *token) {
    if (!*confirming) { *confirming = 1; a->focus = focus_id; return; }
    char c[96]; snprintf(c, sizeof c, "%s", typed); trim(c);
    emit(token); emit_kv("confirm", c); emit_end();
}

/* #695: the effective target state of a room disk -- an operator override if
 * one exists, else the server's `selected`. */
static int room_effective_selected(const App *a, const Machine *m,
                                   const RoomDrawer *d) {
    for (int i = 0; i < a->nroom_selection; i++)
        if (a->room_selection[i].port == d->port &&
            strcmp(a->room_selection[i].mac, m->mac) == 0)
            return a->room_selection[i].selected;
    return d->selected;
}

static void room_toggle_selected(App *a, int mi, int port) {
    if (mi < 0 || mi >= a->st.nmachines || port < 1 || port > MAX_DRAWERS) return;
    Machine *m = &a->st.machines[mi];
    RoomDrawer *d = NULL;
    for (int i = 0; i < m->nroom_drawers; i++)
        if (m->room_drawers[i].present && m->room_drawers[i].port == port) {
            d = &m->room_drawers[i];
            break;
        }
    if (!m->awake || !d) return;
    for (int i = 0; i < a->nroom_selection; i++)
        if (a->room_selection[i].port == port &&
            strcmp(a->room_selection[i].mac, m->mac) == 0) {
            a->room_selection[i].selected = !a->room_selection[i].selected;
            return;
        }
    if (a->nroom_selection < MAX_ROOM_SELECTIONS) {
        int i = a->nroom_selection++;
        char mac[24];                            /* local copy: m->mac and the dest are both inside *a (-Wrestrict) */
        snprintf(mac, sizeof mac, "%s", m->mac);
        snprintf(a->room_selection[i].mac, sizeof a->room_selection[i].mac, "%s", mac);
        a->room_selection[i].port = port;
        a->room_selection[i].selected = !room_effective_selected(a, m, d);
    }
}

/* #714: set (not toggle) one disk's override. */
static void room_set_selected(App *a, const Machine *m, int port, int val) {
    for (int i = 0; i < a->nroom_selection; i++)
        if (a->room_selection[i].port == port &&
            strcmp(a->room_selection[i].mac, m->mac) == 0) {
            a->room_selection[i].selected = val;
            return;
        }
    if (a->nroom_selection < MAX_ROOM_SELECTIONS) {
        int i = a->nroom_selection++;
        char mac[24];                            /* -Wrestrict: both inside *a */
        snprintf(mac, sizeof mac, "%s", m->mac);
        snprintf(a->room_selection[i].mac, sizeof a->room_selection[i].mac, "%s", mac);
        a->room_selection[i].port = port;
        a->room_selection[i].selected = val;
    }
}

/* #714: are all present disks in [begin,end) machines already selected? At
 * least one present disk is required, so an empty room is "not all selected". */
static int room_range_all_selected(const App *a, int begin, int end) {
    int any = 0;
    for (int mi = begin; mi < end; mi++) {
        const Machine *m = &a->st.machines[mi];
        if (!m->awake) continue;
        for (int j = 0; j < m->nroom_drawers; j++) {
            const RoomDrawer *d = &m->room_drawers[j];
            if (!d->present) continue;
            any = 1;
            if (!room_effective_selected(a, m, d)) return 0;
        }
    }
    return any;
}

/* #714: select every present disk in [begin,end) machines, or clear them if
 * they are already all selected (one button that toggles). */
static void room_select_range(App *a, int begin, int end) {
    int val = !room_range_all_selected(a, begin, end);
    for (int mi = begin; mi < end; mi++) {
        Machine *m = &a->st.machines[mi];
        if (!m->awake) continue;
        for (int j = 0; j < m->nroom_drawers; j++) {
            const RoomDrawer *d = &m->room_drawers[j];
            if (d->present) room_set_selected(a, m, d->port, val);
        }
    }
}

static int handle(App *a, const Event *e, const char *auth_cmd, int demo) {
    switch (e->type) {
    case UIEV_MOVE:
        a->ptr_x = e->x; a->ptr_y = e->y; a->ptr_visible = !e->is_touch;
        break;
    case UIEV_CLICK: {
        a->ptr_x = e->x; a->ptr_y = e->y;
        int id = app_hit(a, e->x, e->y);
        if (a->dd_open && !(id >= HIT_OPTION_BASE)) {   /* click outside the open list closes it */
            a->dd_open = 0;
            break;
        }
        /* #695 room-disk toggle. Bounded ranges below are essential: the
         * base ids overlap in value order (300<400), so an unbounded
         * `>= HIT_OPTION_BASE` would swallow room-disk clicks. */
        if (id >= HIT_ROOM_DISK_BASE &&
            id < HIT_ROOM_DISK_BASE + MAX_ROOM_SELECTIONS) {
            int encoded = id - HIT_ROOM_DISK_BASE;
            room_toggle_selected(a, encoded / MAX_DRAWERS, encoded % MAX_DRAWERS + 1);
            break;
        }
        /* #714: machine name toggles all of that machine's disks. */
        if (id >= HIT_ROOM_MACHINE_BASE &&
            id < HIT_ROOM_MACHINE_BASE + MAX_MACHINES) {
            int mi = id - HIT_ROOM_MACHINE_BASE;
            room_select_range(a, mi, mi + 1);
            break;
        }
        /* #649: a tool row, or a disk row of the confirm view */
        if (id >= HIT_TOOL_BASE && id < HIT_TOOL_BASE + MAX_TOOLS) { tool_open(a, id - HIT_TOOL_BASE); break; }
        if (id >= HIT_TOOL_DISK_BASE && id < HIT_TOOL_DISK_BASE + MAX_DISKS) { a->tool_disk_sel = id - HIT_TOOL_DISK_BASE; break; }
        if (id >= HIT_OPTION_BASE && id < HIT_OPTION_BASE + MAX_IMAGES + 1) {
            int i = id - HIT_OPTION_BASE;
            if (a->dd_open == HIT_FOLDER) a->folder_sel = i;
            else if (a->dd_open == HIT_ROOM_IMAGE) a->image_sel = i;
            else if (a->dd_open == HIT_RESTORE_IMAGE) a->restore_image_sel = i;
            a->dd_open = 0;
            break;
        }
        if (id >= HIT_CLASS_BASE && id < HIT_CLASS_BASE + MAX_CLASSES) {
            int i = id - HIT_CLASS_BASE;
            if (i < a->st.nclasses) {
                if (!a->st.classes[i].machines) { toast(a, "לכיתה הזו אין עדיין מחשבים רשומים."); break; }
                emit("class-pick"); emit_kv("group", a->st.classes[i].id); emit_end();
            }
            break;
        }
        if (id >= HIT_DISK_BASE && id < HIT_DISK_BASE + MAX_DISKS) {
            a->chosen_disk = id - HIT_DISK_BASE;
            a->focus = HIT_NAME;                         /* $("#st-name").focus() */
            break;
        }
        switch (id) {
        case HIT_THEME:  a->theme = a->theme == &THEME_DARK ? &THEME_LIGHT : &THEME_DARK; break;
        case HIT_USER: case HIT_PASS: case HIT_NAME: case HIT_DESC: case HIT_FOLDER_NEW:
        case HIT_ROOM_TARGET: case HIT_ROOM_CONFIRM: case HIT_CLASS_CONFIRM:
        case HIT_RESTORE_CONFIRM: case HIT_TOOL_ARG: case HIT_TOOL_CONFIRM:
            a->focus = id; break;
        case HIT_EYE:    a->show_pw = !a->show_pw; break;
        case HIT_SUBMIT: do_login(a, auth_cmd, demo); break;
        case HIT_CAPTURE: case HIT_RESTORE: case HIT_ROOM: case HIT_DIRECT: case HIT_CLASSES: case HIT_TOOLS: return choose_card(a, id);
        case HIT_FOLDER: case HIT_ROOM_IMAGE: case HIT_RESTORE_IMAGE: a->dd_open = id; break;
        case HIT_NEWFOLDER:
            a->newfolder_shown = !a->newfolder_shown;
            if (a->newfolder_shown) a->focus = HIT_FOLDER_NEW;
            break;
        case HIT_START:  start_capture(a); break;
        case HIT_RESTORE_START: start_restore(a); break;
        case HIT_BACK:   go_menu(a); emit1("back"); break;
        case HIT_AGAIN:  a->showing_done = 0; go_menu(a); emit1("again"); break;
        case HIT_ROOM_SELECT_ALL: room_select_range(a, 0, a->st.nmachines); break;
        case HIT_ROOM_OPEN:  open_round(a); break;
        case HIT_ROOM_WAKE:  emit1("room-wake"); break;
        case HIT_ROOM_START: emit1("room-start"); break;
        case HIT_ROOM_CLOSE: close_round(a, &a->room_confirming, a->room_confirm, HIT_ROOM_CONFIRM, "room-close"); break;
        case HIT_CLASS_START: if (class_gate(a)) emit1("class-start"); break;
        case HIT_CLASS_CLOSE: if (class_gate(a)) close_round(a, &a->class_confirming, a->class_confirm, HIT_CLASS_CONFIRM, "class-close"); break;
        case HIT_SMART_REPLACE: emit_smart(a, "smart-replace"); break;
        case HIT_SMART_RESCUE:  emit_smart(a, "smart-rescue");  break;
        case HIT_SMART_SKIP:    emit_smart(a, "smart-skip");    break;
        /* #649 */
        case HIT_TOOL_LIST:  if (!a->tool_running) { a->tools_view = TOOLS_LIST; a->tool_error[0] = 0; } break;
        case HIT_TOOL_RUN:   tool_run(a); break;
        case HIT_TOOL_AGAIN:
            if (a->tool_running || a->tool_sel < 0) break;
            /* an argument or a typed name is asked again -- never replayed */
            if (tools_risk_level(a->tools[a->tool_sel].risk) || a->tools[a->tool_sel].args[0])
                 { a->tool_confirm[0] = 0; a->tools_view = TOOLS_CONFIRM; }
            else tool_run(a);
            break;
        case HIT_TOOL_DOWN:
            if (a->tools_view == TOOLS_OUTPUT) a->tool_scroll += 8; else a->tool_list_scroll += 6;
            break;
        case HIT_TOOL_UP:
            if (a->tools_view == TOOLS_OUTPUT) { a->tool_scroll -= 8; if (a->tool_scroll < 0) a->tool_scroll = 0; }
            else { a->tool_list_scroll -= 6; if (a->tool_list_scroll < 0) a->tool_list_scroll = 0; }
            break;
        }
        break;
    }
    case UIEV_CHAR: {
        size_t n; char *buf = field_buf(a, a->focus, &n);
        if (!buf || !field_visible(a, a->focus)) break;
        if (a->focus == HIT_ROOM_TARGET && (e->ch < '0' || e->ch > '9')) break;   /* type=number */
        field_append(buf, n, e->ch);
        break;
    }
    case UIEV_KEY: {
        if (e->key == KEYSYM_ESC) { a->dd_open = 0; break; }
        if (a->screen == SCREEN_MENU) {
            int ids[5], n = visible_card_ids(a, ids);
            if (e->key == KEYSYM_TAB)        a->menu_focus = (a->menu_focus + 1) % n;
            else if (e->key == KEYSYM_ENTER && a->menu_focus >= 0 && a->menu_focus < n)
                return choose_card(a, ids[a->menu_focus]);
            break;
        }
        int ids[8], n = fields_on(a, ids);
        if (e->key == KEYSYM_TAB && n) {
            int k = 0;
            for (int i = 0; i < n; i++) if (ids[i] == a->focus) k = i + 1;
            a->focus = ids[k % n];
        } else if (e->key == KEYSYM_ENTER) {
            if (a->screen == SCREEN_LOGIN) do_login(a, auth_cmd, demo);   /* the only <form> that submits */
        } else if (e->key == KEYSYM_BACKSPACE) {
            size_t bn; char *buf = field_buf(a, a->focus, &bn);
            if (buf && field_visible(a, a->focus)) field_backspace(buf);
        }
        break;
    }
    default: break;
    }
    return 0;
}

/* ---- the state file --------------------------------------------------------------- */

/* After a reload: the things station.js does on a fresh state. */
static void after_reload(App *a) {
    if (!a->mac[0]) {                                 /* index.html opened without ?mac= */
        snprintf(a->st.msg_title, sizeof a->st.msg_title, "חסר מזהה מכונה");
        snprintf(a->st.msg_sub, sizeof a->st.msg_sub, "הדף נפתח בלי ?mac= — הסוכן אמור לספק אותו.");
    }
    if (a->st.toast[0]) toast(a, a->st.toast);
    if (a->chosen_disk >= a->st.ndisks) a->chosen_disk = -1;
    if (a->folder_sel > a->st.nfolders) a->folder_sel = 0;
    if (a->image_sel > a->st.nimages) a->image_sel = 0;
    if (!a->st.has_round) a->room_confirming = 0;
    if (!a->st.has_session) a->class_confirming = 0;
}

/* ---- --png: every card, both themes, to files -------------------------------------- */

static void sample_state(State *s) {
    memset(s, 0, sizeof *s);
    s->ndisks = 2;
    s->disks[0] = (Disk){ "/dev/sda", "Samsung SSD 870 EVO 500GB", 500107862016ULL, 1, 0 };
    s->disks[1] = (Disk){ "/dev/nvme0n1", "WD Blue SN580 1TB", 1000204886016ULL, 0, 0 };
    s->nfolders = 2;
    snprintf(s->folders[0], sizeof s->folders[0], "Office");
    snprintf(s->folders[1], sizeof s->folders[1], "הנדסאים");
    s->nimages = 2;
    s->images[0] = (Image){ "img_1", "Office 2024 — סטנדרט", "Office" };
    s->images[1] = (Image){ "img_2", "SolidWorks 2025", "הנדסאים" };
    s->nmachines = 3;
    s->machines[0] = (Machine){
        .name = "CLONE-01", .mac = "3C:52:82:A1:00:21", .state = "writing",
        .awake = 1, .joined = 1, .fresh_drawers = 3, .drawer_count = 3,
        .pct = 42, .moving = 1, .nroom_drawers = 3,
        .room_drawers = {
            { .port = 1, .present = 1, .fresh = 1, .selected = 1,
              .size_bytes = 256060514304ULL, .smart = "passed" },
            { .port = 2, .present = 1, .fresh = 1,
              .size_bytes = 500107862016ULL, .smart = "passed" },
            { .port = 3, .present = 1, .fresh = 1,
              .size_bytes = 128035676160ULL, .smart = "warn" } } };
    s->machines[1] = (Machine){
        .name = "CLONE-02", .mac = "3C:52:82:A1:00:22", .state = "done",
        .awake = 1, .joined = 1, .fresh_drawers = 2, .drawer_count = 3,
        .pct = 100, .moving = 1, .nroom_drawers = 2,
        .room_drawers = {
            { .port = 1, .present = 1, .fresh = 1,
              .size_bytes = 256060514304ULL, .smart = "passed" },
            { .port = 2, .present = 1, .fresh = 1,
              .size_bytes = 256060514304ULL, .smart = "passed" } } };
    s->machines[2] = (Machine){
        .name = "CLONE-03", .mac = "3C:52:82:A1:00:23",
        .awake = 0, .drawer_count = 3, .pct = -1 };
    s->menu_class = 1;                        /* the sample shows every card (#880) */
    s->menu_tools = 1;                        /* and the toolbox button (v1.1) */
    s->nclasses = 3;
    s->classes[0] = (ClassGroup){ "grp_b12", "כיתה 12", 14 };
    s->classes[1] = (ClassGroup){ "grp_b7", "כיתה 7", 30 };
    s->classes[2] = (ClassGroup){ "grp_lab", "מעבדת רשתות", 0 };
    s->task = TASK_RUNNING;
    snprintf(s->task_name, sizeof s->task_name, "Office 2024 — סטנדרט");
    snprintf(s->task_disk, sizeof s->task_disk, "/dev/sda");
    s->pct = 42; s->moving = 1; s->bytes = 23089744896ULL;
    snprintf(s->round_image, sizeof s->round_image, "Office 2024 — סטנדרט");
    s->wave_number = 2; s->wave_open = 1; s->written = 7; s->target = 24; s->ready = 5; s->remaining = 17;
    s->elapsed_s = 3725; s->rate_bps = 52000000; s->eta_s = 610;      /* #410 */
    s->updated = (long long)time(NULL) - 3;
    snprintf(s->sess_image, sizeof s->sess_image, "Office 2024 — סטנדרט");
    snprintf(s->sess_prefix, sizeof s->sess_prefix, "B12");
    snprintf(s->sess_group, sizeof s->sess_group, "כיתה 12");
    s->sess_open = 1; s->joined = 3; s->expected = 14; s->starts_in = 272;
    s->ndrawers = 3;
    s->drawers[0] = (Drawer){ 1, "sda", "writing", "", 44814663680ULL, 85899345920ULL, 98000000, 419 };   /* #410 */
    s->drawers[1] = (Drawer){ 2, "sdb", "failed", "", 0, 85899345920ULL, -1, -1 };
    s->drawers[2] = (Drawer){ 3, "sdc", "waiting", "", 0, 85899345920ULL, -1, -1 };
    snprintf(s->cloner_image, sizeof s->cloner_image, "Office 2024 — סטנדרט");
    s->smart_pending = 1; s->smart_port = 2;
    snprintf(s->smart_nonce, sizeof s->smart_nonce, "1-2");
    snprintf(s->smart_verdict, sizeof s->smart_verdict, "fail");
    snprintf(s->smart_reason, sizeof s->smart_reason, "pending");
    snprintf(s->msg_title, sizeof s->msg_title, "המחשב אינו רשום");
    snprintf(s->msg_sub, sizeof s->msg_sub, "רשמו אותו בקונסולה כמחשב בניית אימג'ים ורעננו.");
    snprintf(s->machine_name, sizeof s->machine_name, "BUILD-01");   /* #1073: the restore card's confirm field */
}

static const char *PNG_CARDS[] = { "login", "menu", "pick", "progress", "done", "room", "room-live",
                                   "class", "class-live", "cloner", "message", "restore", "direct",
                                   "tools", "tools-confirm", "tools-output" };

/* Put the App in the state that routes to <card>; the flags that override
 * the route (message, task, round, session, done) are cleared first so a
 * user-supplied --state file renders every card, not just its own. */
static void png_setup(App *a, const State *base, const char *card) {
    a->st = *base;
    a->st.msg_title[0] = a->st.msg_sub[0] = 0;
    a->st.task = TASK_NONE; a->st.has_round = 0; a->st.has_session = 0;
    a->showing_done = a->watching = a->force_progress = a->force_cloner = 0;
    a->signed_in = 1; a->admin = 1; a->mode = MODE_MENU;
    a->chosen_disk = -1; a->room_confirming = a->class_confirming = 0; a->dd_open = 0;
    snprintf(a->signed_user, sizeof a->signed_user, "%s", a->user[0] ? a->user : "admin");
    if (!strcmp(card, "login"))         { a->signed_in = 0; }
    else if (!strcmp(card, "pick"))     { a->mode = MODE_CAPTURE; if (base->ndisks) { a->chosen_disk = 0; snprintf(a->name, sizeof a->name, "Office 2024 — סטנדרט"); a->folder_sel = base->nfolders ? 1 : 0; } }
    else if (!strcmp(card, "progress")) { a->st.task = base->task == TASK_PENDING ? TASK_PENDING : TASK_RUNNING; }
    else if (!strcmp(card, "done"))     { a->st.task = TASK_DONE; a->watching = 1; }
    else if (!strcmp(card, "room"))     { a->mode = MODE_ROOM; a->room_target_set = 0; a->nroom_selection = 0; }
    else if (!strcmp(card, "room-live")){ a->mode = MODE_ROOM; a->st.has_round = 1; }
    else if (!strcmp(card, "direct"))   { a->mode = MODE_DIRECT; a->room_target_set = 0; a->nroom_selection = 0; }   /* #715 */
    else if (!strcmp(card, "class"))    { a->mode = MODE_CLASSES; }
    else if (!strcmp(card, "class-live")){ a->mode = MODE_CLASSES; a->st.has_session = 1; }
    else if (!strcmp(card, "cloner"))   { a->force_cloner = 1; }
    else if (!strcmp(card, "message"))  { snprintf(a->st.msg_title, sizeof a->st.msg_title, "%s", base->msg_title);
                                          snprintf(a->st.msg_sub, sizeof a->st.msg_sub, "%s", base->msg_sub); }
    else if (!strcmp(card, "restore"))  { a->mode = MODE_RESTORE; if (base->nimages) a->restore_image_sel = 1; }
    else if (!strncmp(card, "tools", 5)) {                                       /* #649 */
        a->mode = MODE_TOOLS; a->tools_view = TOOLS_LIST; a->tool_sel = -1; a->tool_running = 0;
        if (!a->ntools) tools_sample(a);
        int wipe = -1, smart = -1;
        for (int i = 0; i < a->ntools; i++) {
            if (!strcmp(a->tools[i].id, "wipe")) wipe = i;
            if (!strcmp(a->tools[i].id, "smart-health")) smart = i;
        }
        if (!strcmp(card, "tools-confirm")) {
            a->tool_sel = wipe >= 0 ? wipe : 0; a->tools_view = TOOLS_CONFIRM; a->tool_disk_sel = base->ndisks ? 0 : -1;
            snprintf(a->tool_confirm, sizeof a->tool_confirm, "BUILD-0"); a->focus = HIT_TOOL_CONFIRM;
        } else if (!strcmp(card, "tools-output")) {
            a->tool_sel = smart >= 0 ? smart : 0; a->tools_view = TOOLS_OUTPUT; a->tool_rc = 2;
            tool_output_set(a, "/dev/sda (Samsung SSD 870 EVO 500GB): תקין -- SMART overall-health PASSED\n"
                                "/dev/nvme0n1 (WD Blue SN580 1TB): לא הצלחנו לבדוק -- smartctl לא הצליח לקרוא את הדיסק (rc 2). זה לא \"תקין\".\n"
                                "\nמה זה אומר: PASSED הוא הדיווח של הדיסק על עצמו, לא ערובה; כשל ב-SMART הוא ראיה מספקת להחלפה.\n");
        }
    }
}

static int render_png(App *a, const State *base, const char *prefix, int w, int h) {
    const Theme *themes[2] = { &THEME_LIGHT, &THEME_DARK };
    int ncards = (int)(sizeof PNG_CARDS / sizeof PNG_CARDS[0]);
    for (int c = 0; c < ncards; c++) for (int t = 0; t < 2; t++) {
        a->theme = themes[t];
        png_setup(a, base, PNG_CARDS[c]);
        cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, w, h);
        cairo_t *cr = cairo_create(sf);
        app_draw(a, cr, w, h);
        cairo_destroy(cr);
        char path[512];
        snprintf(path, sizeof path, "%s-%s-%s.png", prefix, PNG_CARDS[c], themes[t]->id);
        cairo_status_t st = cairo_surface_write_to_png(sf, path);
        cairo_surface_destroy(sf);
        if (st != CAIRO_STATUS_SUCCESS) { fprintf(stderr, "native-gui: %s: %s\n", path, cairo_status_to_string(st)); return 1; }
        /* the route must have landed on the card we asked for -- positive evidence */
        static const Screen want[] = { SCREEN_LOGIN, SCREEN_MENU, SCREEN_PICK, SCREEN_PROGRESS, SCREEN_DONE,
                                       SCREEN_ROOM, SCREEN_ROOM, SCREEN_CLASS, SCREEN_CLASS, SCREEN_CLONER, SCREEN_MESSAGE,
                                       SCREEN_RESTORE,
                                       SCREEN_ROOM, /* "direct" (#715): the room screen in MODE_DIRECT */
                                       SCREEN_TOOLS, SCREEN_TOOLS, SCREEN_TOOLS /* #649: list, confirm, output */ };
        /* One entry per card, or want[c] reads past the table -- that is exactly
         * what the lab saw when "direct" was added to PNG_CARDS alone. */
        _Static_assert(sizeof want / sizeof want[0] == sizeof PNG_CARDS / sizeof PNG_CARDS[0],
                       "want[] must have one Screen per PNG_CARDS entry");
        if (a->screen != want[c]) { fprintf(stderr, "native-gui: %s routed to screen %d, not %d\n", path, a->screen, want[c]); return 1; }
        printf("wrote %s\n", path);
    }
    return 0;
}

/* ---- main ------------------------------------------------------------------------ */

int main(int argc, char **argv) {
    App a;
    memset(&a, 0, sizeof a);
    a.theme = &THEME_LIGHT;                         /* index.html: light unless chosen */
    a.focus = HIT_USER;
    a.menu_focus = -1;
    a.chosen_disk = -1;
    a.st.pct = -1;
    snprintf(a.title, sizeof a.title, "מחשב בניית אימג'ים");
    const char *auth_cmd = NULL, *png = NULL, *backend = "auto", *fb_file = NULL, *screen = NULL, *role = "deploy";
    StateFile sf_state; memset(&sf_state, 0, sizeof sf_state);
    int demo = 0, no_input = 0, pw = 1280, ph = 800;

    for (int i = 1; i < argc; i++) {
        const char *o = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(o, "--demo")) demo = 1;
        else if (!strcmp(o, "--no-input")) no_input = 1;
        else if (!strcmp(o, "--help") || !strcmp(o, "-h")) { fputs(USAGE, stdout); return 0; }
        else if (!v) { fputs(USAGE, stderr); return 2; }
        else if (!strcmp(o, "--mac")) snprintf(a.mac, sizeof a.mac, "%s", argv[++i]);
        else if (!strcmp(o, "--ip")) snprintf(a.ip, sizeof a.ip, "%s", argv[++i]);
        else if (!strcmp(o, "--title")) snprintf(a.title, sizeof a.title, "%s", argv[++i]);
        else if (!strcmp(o, "--user")) snprintf(a.user, sizeof a.user, "%s", argv[++i]);
        else if (!strcmp(o, "--theme")) { a.theme = !strcmp(argv[++i], "dark") ? &THEME_DARK : &THEME_LIGHT; }
        else if (!strcmp(o, "--backend")) backend = argv[++i];
        else if (!strcmp(o, "--fb-file")) fb_file = argv[++i];
        else if (!strcmp(o, "--auth-cmd")) auth_cmd = argv[++i];
        else if (!strcmp(o, "--state")) snprintf(sf_state.path, sizeof sf_state.path, "%s", argv[++i]);
        else if (!strcmp(o, "--screen")) screen = argv[++i];
        else if (!strcmp(o, "--signed-in")) { snprintf(a.signed_user, sizeof a.signed_user, "%s", argv[++i]); a.signed_in = 1; }
        else if (!strcmp(o, "--role")) role = argv[++i];
        else if (!strcmp(o, "--png")) png = argv[++i];
        else if (!strcmp(o, "--size")) { if (sscanf(argv[++i], "%dx%d", &pw, &ph) != 2 || pw < 320 || ph < 240) { fputs(USAGE, stderr); return 2; } }
        else { fprintf(stderr, "unknown option %s\n%s", o, USAGE); return 2; }
    }
    a.admin = a.signed_in && strcmp(role, "admin") == 0;
    if (screen) {
        if (!strcmp(screen, "progress"))   a.force_progress = 1;
        else if (!strcmp(screen, "class")) a.mode = MODE_CLASSES;
        else if (!strcmp(screen, "cloner")) { a.force_cloner = 1; a.theme = &THEME_LIGHT; }  /* cloner is light-only */
        else { fprintf(stderr, "native-gui: --screen must be progress, class or cloner\n%s", USAGE); return 2; }
    }
    setvbuf(stdout, NULL, _IOLBF, 0);               /* records reach the agent's pipe as they happen */

    /* The fonts index.html asks for must really be there; a fallback face
     * would render, and nobody would notice the wrong font until a
     * classroom did. Positive evidence, not absence of an error. */
    char missing[128];
    if (!text_fonts_present(missing, sizeof missing)) {
        fprintf(stderr, "native-gui: font family not found: %s (FONTCONFIG_FILE=%s)\n",
                missing, getenv("FONTCONFIG_FILE") ? getenv("FONTCONFIG_FILE") : "(unset)");
        return 1;
    }

    if (png) {
        State base;
        if (sf_state.path[0]) {
            if (state_poll(&sf_state, &base) < 0) return 1;
        } else {
            sample_state(&base);
        }
        return render_png(&a, &base, png, pw, ph);
    }

    a.tool_sel = -1; a.tool_disk_sel = -1; a.tool_rc = -1;
    if (sf_state.path[0]) {
        if (state_poll(&sf_state, &a.st) < 0) return 1;   /* a missing file at start is a wrong invocation, say so */
        after_reload(&a);
        /* #649: the toolbox files live next to the state file ($GUI_DIR) */
        snprintf(a.tools_dir, sizeof a.tools_dir, "%s", sf_state.path);
        char *slash = strrchr(a.tools_dir, '/');
        if (slash) *slash = 0; else snprintf(a.tools_dir, sizeof a.tools_dir, ".");
        tools_poll(&a);
    } else {
        after_reload(&a);
    }

    char err[1024];
    Backend *b = backend_open(backend, fb_file, err, sizeof err);
    if (!b) { fprintf(stderr, "native-gui: cannot open a display: %s\n", err); return 1; }
    int W = backend_width(b), H = backend_height(b);
    fprintf(stderr, "native-gui: display %dx%d\n", W, H);

    /* A missing input device is NOT fatal: a USB keyboard/mouse often
     * enumerates a second or two after boot. The build machine used to come
     * up to a text login because the one startup scan ran before the keyboard
     * was ready. Keep the display up; input_rescan() in the loop grabs a
     * device the moment it appears (and a hot-plugged mouse too). */
    Input *in = no_input ? NULL : input_open(W, H);

    signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
    cairo_surface_t *sf = backend_surface(b);
    /* Scene cache: app_draw() is Pango-heavy and was re-run every frame, so a
     * mere pointer move re-laid-out the whole screen -- the mouse felt laggy.
     * Now app_draw writes into this cache only when the scene actually changes
     * (state reload, screen/focus/typing/click, toast, or a hover-highlight
     * crossing); a pointer-only move just blits the cache and overlays the
     * cursor. */
    cairo_surface_t *scene = cairo_image_surface_create(CAIRO_FORMAT_RGB24, W, H);
    int done = 0, scene_dirty = 1, need_present = 1, last_hover = -1;
    long long last_age_tick = 0;                    /* #410 */
    struct pollfd fds[32];
    while (!stop_now && !done) {
        if (a.toast[0] && now_s() >= a.toast_until) { a.toast[0] = 0; scene_dirty = 1; }
        if (scene_dirty) {
            cairo_t *cr = cairo_create(scene);
            app_draw(&a, cr, W, H);
            cairo_destroy(cr);
            scene_dirty = 0; need_present = 1;
        }
        if (need_present) {
            cairo_t *cr = cairo_create(sf);
            cairo_set_source_surface(cr, scene, 0, 0);
            cairo_paint(cr);
            app_draw_pointer(&a, cr);
            cairo_destroy(cr);
            backend_present(b);
            need_present = 0;
        }

        /* setInterval(poll, 2000): wake at least every 2 s for the state
         * file, sooner to take a toast down. */
        int timeout = 2000;
        if (a.toast[0]) { int left = (int)((a.toast_until - now_s()) * 1000) + 1; if (left < timeout) timeout = left > 0 ? left : 1; }
        if (a.tool_running) { timeout = 120; a.tool_spin += 0.4; scene_dirty = 1; }   /* #649: the spinner turns */
        if (in) input_rescan(in);               /* pick up a late/hot-plugged keyboard or mouse */
        int n = in ? input_fill_pollfds(in, fds, 32) : 0;
        int r = poll(n ? fds : NULL, n, timeout);
        if (r < 0) { if (errno == EINTR) continue; break; }
        if (r > 0 && in) {
            input_pump(in, fds, n);
            Event e; int moved = 0;
            while (!done && input_next(in, &e)) {
                if (e.type == UIEV_MOVE) moved = 1;
                else scene_dirty = 1;           /* char/key/click change the scene */
                done = handle(&a, &e, auth_cmd, demo);
            }
            if (moved) {
                need_present = 1;               /* the cursor moved */
                int h = a.ptr_visible ? app_hit(&a, a.ptr_x, a.ptr_y) : -1;
                if (h != last_hover) { last_hover = h; scene_dirty = 1; }  /* a hover highlight changed */
            }
        }
        if (sf_state.path[0] && state_poll(&sf_state, &a.st) > 0) { after_reload(&a); scene_dirty = 1; }
        if (tools_poll(&a)) scene_dirty = 1;                                        /* #649 */
        /* #410: "עודכן לפני N שנ'" must keep counting when the state file
         * stops changing -- that is the whole point of the stamp. One
         * redraw per wake (2 s) while a stamp is on screen. */
        if (a.st.updated > 0 && (long long)time(NULL) != last_age_tick) { last_age_tick = (long long)time(NULL); scene_dirty = 1; }
    }
    if (in) input_close(in);
    cairo_surface_destroy(scene);
    backend_close(b);
    return done ? 0 : 1;                            /* 1: stopped by a signal */
}
