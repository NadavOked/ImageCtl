/* Application state for every card of server/static/station/index.html
 * and the hit-test table that drawing fills in. Layout lives in the
 * screens_*.c files and registers where it put each clickable thing, so
 * the event loop never re-derives geometry.
 *
 * Two kinds of state live here: what the operator did (typed, chose,
 * opened) and what the agent said through the --state file (disks,
 * machines, the running task). The second kind is the State struct and is
 * replaced wholesale on every re-read; the first survives it. */
#ifndef IMAGECTL_UI_H
#define IMAGECTL_UI_H

#include <cairo.h>
#include "draw.h"
#include "install_state.h"
#include "theme.h"

#define MAX_HITS    320     /* #695: 30 machines x 8 disks need room-disk hit targets */
#define MAX_DISKS     8
#define MAX_FOLDERS  32
#define MAX_IMAGES   64
#define MAX_MACHINES 32
#define MAX_CLASSES  16
#define MAX_DRAWERS   8
#define MAX_ROOM_SELECTIONS (MAX_MACHINES * MAX_DRAWERS)
/* #649: the IT toolbox -- rows of $GUI_DIR/tools, and one tool's output. */
#define MAX_TOOLS      64
#define TOOL_OUT_MAX   32768
#define TOOL_OUT_LINES 512

enum {
    HIT_NONE = 0,
    HIT_THEME,          /* #st-theme */
    HIT_USER,           /* #st-user */
    HIT_PASS,           /* #st-pass */
    HIT_EYE,            /* #st-eye */
    HIT_SUBMIT,         /* .btn.primary "כניסה" */
    HIT_CAPTURE,        /* #st-menu-capture */
    HIT_LOGOUT,         /* menu: local session logout */
    HIT_RESTORE,        /* restore to this machine's disk (#382; no HTML id yet) */
    HIT_ROOM,           /* #st-menu-room */
    HIT_DIRECT,         /* #715: deploy THIS disk directly to the room (native-only) */
    HIT_CLASSES,        /* #st-menu-classes */
    /* #st-pick */
    HIT_NAME,           /* #st-name */
    HIT_DESC,           /* #st-desc */
    HIT_FOLDER,         /* #st-folder (select) */
    HIT_NEWFOLDER,      /* #st-newfolder "+ חדשה" */
    HIT_FOLDER_NEW,     /* #st-folder-new */
    HIT_START,          /* #st-start "התחל קליטה" */
    HIT_BACK,           /* #st-back-capture / #room-back / #cls-back "חזרה" */
    /* #st-done */
    HIT_AGAIN,          /* #st-again "קליטה נוספת" */
    /* #706 restore-to-local-disk screen (native-only) */
    HIT_RESTORE_IMAGE,  /* image picker (select) */
    HIT_RESTORE_CONFIRM,/* the ERASE confirm field */
    HIT_RESTORE_START,  /* "התחל שחזור" */
    /* #st-room (room.js) */
    HIT_ROOM_IMAGE,     /* #room-image (select) */
    HIT_ROOM_TARGET,    /* #room-target */
    HIT_ROOM_OPEN,      /* #room-open */
    HIT_ROOM_WAKE,      /* #room-wake */
    HIT_ROOM_START,     /* #room-start */
    HIT_ROOM_CLOSE,     /* #room-close */
    HIT_ROOM_CONFIRM,   /* #room-confirm-text */
    HIT_ROOM_SELECT_ALL,/* #714: select/clear every disk in the room */
    /* #st-class (classes.js) */
    HIT_CLASS_START,    /* #cls-start */
    HIT_CLASS_CLOSE,    /* #cls-close */
    HIT_CLASS_CONFIRM,  /* #cls-confirm-text */
    /* the cloner's SMART choice (no HTML id: this screen is native-only) */
    HIT_SMART_REPLACE,
    HIT_SMART_RESCUE,
    HIT_SMART_SKIP,
    /* #649: the toolbox (native-only; build machine's menu) */
    HIT_TOOLS,          /* the "כלים" button at the menu's top-left */
    HIT_TOOL_LIST,      /* back to the list from confirm / output */
    HIT_TOOL_RUN,       /* confirm view: run */
    HIT_TOOL_AGAIN,     /* output view: run again */
    HIT_TOOL_ARG,       /* confirm view: the free-text argument field */
    HIT_TOOL_CONFIRM,   /* confirm view: the typed machine name (principle 7) */
    HIT_TOOL_UP,        /* list / output: scroll */
    HIT_TOOL_DOWN,
    /* #1188: first-boot installer. */
    HIT_INSTALL_BACK,
    HIT_INSTALL_NEXT,
    HIT_INSTALL_PRIMARY,
    HIT_INSTALL_SECONDARY,
    HIT_INSTALL_CHECK_PRIMARY,
    HIT_INSTALL_DHCP,
    HIT_INSTALL_STATIC,
    HIT_INSTALL_PRIMARY_URL,
    HIT_INSTALL_ADDRESS,
    HIT_INSTALL_NETMASK,
    HIT_INSTALL_GATEWAY,
    HIT_INSTALL_DNS,
    HIT_INSTALL_HOSTNAME,
    HIT_INSTALL_PASSWORD,
    HIT_INSTALL_CONFIRM,
    HIT_INSTALL_CURRENT_PASSWORD,
    HIT_INSTALL_RETRY,
    HIT_INSTALL_OUTPUT_UP,
    HIT_INSTALL_OUTPUT_DOWN,
    HIT_INSTALL_SUMMARY_BASE = 1000,
    HIT_INSTALL_NIC_BASE = 1020,
    /* ranges: base + index */
    HIT_DISK_BASE   = 100,   /* .disk-card[data-dev] */
    HIT_CLASS_BASE  = 200,   /* .menu-card[data-class] */
    HIT_OPTION_BASE = 300,   /* a row of the open <select> list */
    /* #695 room disk: +(machine_index * MAX_DRAWERS)+(port-1) */
    HIT_ROOM_DISK_BASE = 400,
    /* #714 machine name: +machine_index -- toggles all that machine's disks */
    HIT_ROOM_MACHINE_BASE = 700,
    /* #649: a tool row (+index into a->tools) and a disk row of the
     * confirm view (+index into st.disks) */
    HIT_TOOL_BASE = 800,
    HIT_TOOL_DISK_BASE = 900,
};

typedef struct { Rect r; int id; } Hit;

typedef enum {
    SCREEN_LOGIN, SCREEN_MENU, SCREEN_PICK, SCREEN_PROGRESS, SCREEN_DONE,
    SCREEN_ROOM, SCREEN_CLASS, SCREEN_CLONER, SCREEN_STANDBY, SCREEN_MESSAGE, SCREEN_RESTORE,
    SCREEN_TOOLS,       /* #649 */
    SCREEN_INSTALL      /* #1188 */
} Screen;

/* station.js MODE: null (menu) / "capture" / "room" / "classes"; #706 adds restore */
/* #715: MODE_DIRECT is the room screen without an image picker -- the source
 * is this machine's disk; open emits the chosen drawers as target_slots. */
typedef enum { MODE_MENU, MODE_CAPTURE, MODE_ROOM, MODE_CLASSES, MODE_RESTORE, MODE_DIRECT, MODE_TOOLS } Mode;

typedef enum { TASK_NONE, TASK_PENDING, TASK_RUNNING, TASK_DONE, TASK_FAILED } TaskState;

typedef struct { char dev[32], model[80]; unsigned long long size_bytes; int has_data, removable; } Disk;
typedef struct { char id[64], name[96], folder[64]; } Image;
/* #695: one physical slot of a room machine, for the distribute grid. present
 * is separate from size_bytes (a real disk may report 0). port 0 is the
 * legacy/null-port fallback assigned by state_parse(). */
typedef struct {
    int port, present, fresh, selected;
    char serial[80], model[80], smart[16];
    unsigned long long size_bytes;
} RoomDrawer;
typedef struct {
    char name[64], mac[24], state[24], error[120];
    int awake, joined, fresh_drawers, drawer_count;
    int pct;            /* 0..100, or -1 when the total is unknown (Progress.view) */
    int moving;         /* bytes_written > 0 -- distinguishes the two unknown states */
    RoomDrawer room_drawers[MAX_DRAWERS];
    int nroom_drawers;
} Machine;
typedef struct { char id[64], label[80]; int machines; } ClassGroup;
/* One tray of a cloning machine. Labelled/sorted by SATA port, not device
 * order (roomflow.sh: sdb is not "disk 2"). port 0 = no slot derived. */
typedef struct {
    int port;
    char dev[32], state[24], error[160];
    unsigned long long bytes, total;
    /* #410: from the kiosk's local counter (clonergui.sh:cloner_gui_pace).
     * rate_bps <= 0 / eta_s < 0 = not measured -- never drawn as 0. */
    long long rate_bps; int eta_s;
} Drawer;
/* A physically connected disk shown on the cloner's IDLE screen (no round yet),
 * so the operator sees what is plugged in before a broadcast starts. */
typedef struct { int port; char model[48]; unsigned long long size; char smart[16];
                 char cause[16]; /* #874: cable|disk|unclassified for failed_last, else "" */ } IdleDisk;

/* Everything the --state file can say. See README "The --state file". */
typedef struct State {
    Disk disks[MAX_DISKS];              int ndisks;
    char folders[MAX_FOLDERS][64];      int nfolders;
    Image images[MAX_IMAGES];           int nimages;
    Machine machines[MAX_MACHINES];     int nmachines;
    ClassGroup classes[MAX_CLASSES];    int nclasses;
    Drawer drawers[MAX_DRAWERS];        int ndrawers;
    IdleDisk idisks[MAX_DRAWERS];       int nidisks;   /* cloner idle inventory */
    char disk_probe[16];                /* drives / no_disks / no_ports / unchecked */

    /* the build machine's own task (station.js drawProgress / drawDone) */
    TaskState task;
    int task_direct;                    /* #715: task.type == direct_send (sending, not capturing) */
    char task_name[96], task_disk[32], task_error[200];
    int pct, moving, partition;         /* pct -1 = unknown total */
    unsigned long long bytes;           /* bytes_written */
    char title[128], sub[200];          /* overrides for #st-prog-title / #st-prog-sub */

    /* message=title|sub forces #st-message (like !state.known) */
    char msg_title[96], msg_sub[200];

    /* room.js round */
    int has_round; char round_image[96];
    int wave_number, wave_open, written, target, ready, remaining;
    /* #410: round=...|elapsed_s|rate_bps|eta_s from the server (room.py
     * _wave_pace); -1 = not measured. updated= is the epoch the agent wrote
     * this snapshot, so the screen can say how old it is (0 = no stamp). */
    int elapsed_s, eta_s; long long rate_bps, updated;
    /* classes.js live session */
    int has_session; char sess_image[96], sess_prefix[32], sess_group[80];
    int sess_open, joined, expected, starts_in;
    /* #880: menu_class=0|1 from guistate.sh -- the "הפצה לכיתות" card is
     * offered only when the server's switch is on. Absent = 0 (v1 default). */
    int menu_class;
    /* menu_tools=0|1 from guistate.sh -- v1 ships without the toolbox
     * (Nadav, 19/09; v1.1 turns it on): the "כלים" button is drawn only
     * when the server said so. Absent = 0. */
    int menu_tools;
    /* #1073: machine_name=<registered name> from guistate.sh -- the restore
     * screen's typed confirmation (principle 7). Empty = the registry has
     * none, and the screen refuses to start rather than match "" to "". */
    char machine_name[64];

    /* the cloner screen (clonergui.sh): image name + a pending SMART choice.
     * smart_nonce is the request's identity, drawn with the prompt and echoed
     * back on the click so the bridge binds it to the exact request. */
    char cloner_image[96];
    int smart_pending, smart_port;
    char smart_nonce[32], smart_verdict[16], smart_reason[96];

    /* #1090: positive evidence from the agent's latest hello. -1 means the
     * key was absent, which is different from a measured zero seconds/HTTP 0. */
    int hello_age, hello_rc;

    /* Parser integrity is visible, not only stderr: a partial picture must
     * never look complete to the operator. */
    int hidden_disks, hidden_machines, rejected_lines, stale;
    char state_warning[160];

    char form_error[160], room_error[160], class_error[160], toast[160];
} State;

/* #649: one row of $GUI_DIR/tools (id|domain|title|risk|args). risk is
 * ro / rw / destroy; anything else is treated as destroy, like tools.sh. */
typedef struct { char id[48], domain[24], title[120], risk[12], args[96]; } Tool;
typedef enum { TOOLS_LIST, TOOLS_CONFIRM, TOOLS_OUTPUT } ToolsView;
/* A polled file's identity: mtime (with the nanoseconds tmpfs keeps), size
 * and inode -- the bridge unlinks and recreates tool-result per run. */
typedef struct { long sec, nsec, size, ino; int seen; } FileMark;

typedef struct App {
    Screen screen;              /* derived by app_route() from the rest */
    const Theme *theme;

    char title[128];            /* #st-title */
    char mac[40];               /* #st-mac  (dir=ltr, mono) */
    char ip[48];                /* #st-ip */

    /* login */
    char user[64];
    char pass[128];
    int show_pw;                /* #st-eye toggled */
    int focus;                  /* HIT id of the focused text field */
    char error[200];            /* #st-login-error */

    /* session */
    int signed_in;              /* station.js SIGNED_IN */
    char signed_user[64];       /* "מחוברים כ־<user>" */
    int admin;                  /* station.js: capture card only for role admin */
    int menu_focus;             /* keyboard focus among visible cards, -1 = none */
    Mode mode;
    int force_progress;         /* --screen progress: show it even with no task */
    int force_cloner;           /* --screen cloner: the cloning machine's own screen */
    int force_standby;          /* --screen standby / idle cloner */
    int fixed_clock;            /* --png: header clock is a constant, so two renders
                                   a minute apart are byte-identical (test flake, 19/09) */
    int watching;               /* #st-progress was on screen (drawDone trigger) */
    int showing_done;           /* stay on #st-done until "קליטה נוספת" */
    char done_title[64], done_sub[240];

    /* #st-pick */
    int chosen_disk;            /* index into st.disks, -1 = none (CHOSEN_DISK) */
    char name[96], desc[160];
    int folder_sel;             /* 0 = "ללא תיקייה", i = st.folders[i-1] */
    int newfolder_shown;        /* #st-folder-new visible */
    char folder_new[64];

    /* #st-room */
    int image_sel;              /* 0 = "בחרו אימג'…", i = st.images[i-1] */
    char room_target[8];        /* #room-target, digits */
    int room_target_set;        /* value seeded once per setup skeleton */
    int room_confirming;        /* #room-confirm shown */
    char room_confirm[96];
    /* #706 restore-to-local-disk: own selection so a room reload never
     * authorizes a local erase, and its own confirm field -- the machine's
     * registered name since #1073 (was the word ERASE). */
    int restore_image_sel;      /* 0 = "בחרו אימג'…", i = st.images[i-1] */
    char restore_confirm[96];
    /* #695: per-disk target overrides, keyed by machine identity + physical
     * port -- outside State so a 2 s state reload does not erase clicks. */
    struct { char mac[24]; int port, selected; } room_selection[MAX_ROOM_SELECTIONS];
    int nroom_selection;

    /* #st-class */
    int class_confirming;
    char class_confirm[96];

    int dd_open;                /* HIT_FOLDER | HIT_ROOM_IMAGE, or 0 */

    /* #649: the toolbox. The list and the result are two files next to the
     * state file ($GUI_DIR/tools, $GUI_DIR/tool-result), polled like it. */
    char tools_dir[512];        /* dirname of --state; empty = no bridge */
    Tool tools[MAX_TOOLS];      int ntools;
    char tools_machine[64];     /* machine= header: the name to type */
    int tools_loaded;           /* the tools file was read at least once */
    FileMark tools_mark, result_mark;
    ToolsView tools_view;
    int tool_sel;               /* index into tools, -1 = none */
    int tool_disk_sel;          /* confirm view disk picker: index into st.disks, -1 */
    char tool_arg[96], tool_confirm[96], tool_error[160];
    int tool_list_scroll, tool_scroll;
    int tool_running, tool_rc;  /* rc -1 = no result yet */
    long tool_seq;              /* seq of the last result consumed */
    double tool_spin;           /* spinner angle while running */
    char tool_out[TOOL_OUT_MAX];
    int tool_line_off[TOOL_OUT_LINES]; int tool_nlines;

    char toast[160];            /* #toast; empty = hidden */
    double toast_until;         /* monotonic seconds */

    State st;

    /* #1188: local UI state; validation and apply remain in wizard/core.py. */
    InstallState install;

    /* pointer */
    double ptr_x, ptr_y;
    int ptr_visible;            /* a mouse moved; touch leaves no cursor */

    Hit hits[MAX_HITS];
    int nhits;
    Rect clip; int clip_on;     /* .sbody overflow: hits outside are dropped */
} App;

/* Draw the whole screen into <cr> (W x H pixels) and rebuild the hit table. */
void app_draw(App *a, cairo_t *cr, int W, int H);
void app_draw_pointer(App *a, cairo_t *cr);
/* Which registered element is under (x, y); topmost wins. */
int app_hit(const App *a, double x, double y);
/* Decide a->screen from the task, the message, the session and the mode --
 * the order of station.js poll(). Called before every draw. */
void app_route(App *a);

/* The screens (one file each group); called by app_draw. */
void screen_login(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_menu(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_pick(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_progress(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_done(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_message(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_room(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_class(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_cloner(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_standby(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_restore(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_tools(App *a, cairo_t *cr, double W, double H, double head_h);
void screen_install(App *a, cairo_t *cr, double W, double H);

/* #649 (screens_tools.c): the tools / tool-result files next to --state.
 * tools_poll re-reads what changed; returns 1 when the scene must redraw.
 * tools_parse fills a->tools from text (the same parser, testable by
 * --png with a state file). tools_risk_level: 0 ro, 1 rw, 2 destroy. */
int tools_poll(App *a);
void tools_parse(App *a, const char *text);
int tools_risk_level(const char *risk);
void tools_sample(App *a);
void tool_output_set(App *a, const char *text);

#endif
