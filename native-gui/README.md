# native-gui — the station screens without a browser

The build machine's operator screens were authored in HTML
(`server/static/station/`). Displaying them costs a browser in a RAM-resident
initramfs: 120 MB (WPE WebKit) to 315 MB (Chromium) for 57 KB of HTML
(`docs/research/R03-network-gui.md`, `R09-hebrew-ui-without-a-browser.md`).
R09's finding is that the browser was chosen for **Hebrew** — bidi and
shaping — and those come from two small libraries, not from a browser.

This directory is **every card of `index.html`**, redrawn natively, with the
shared header, light and dark themes, RTL. It is additive: `server/`,
`agent/` and the HTML are untouched. The login and menu compile on Debian 13
and the Hebrew smoke test passes there; **the six cards added after them have
not been compiled yet** — see "What I could not verify".

| card (`index.html` id) | native screen | login | what it shows |
|---|---|---|---|
| `#st-login` | `SCREEN_LOGIN` | — | כניסה: user, password + eye, error, כניסה |
| `#st-menu` | `SCREEN_MENU` | gated | מה עושים? four cards, #1073 order: room · direct · restore (#382/#706) · capture (admin-only, last); the class card only with `menu_class=1` (v2) |
| `#st-pick` | `SCREEN_PICK` | gated | קליטת אימג' חדש: the internal disks, then name / description / folder + "+ חדשה", התחל קליטה, חזרה, the hint |
| `#st-progress` | `SCREEN_PROGRESS` | **open** | קולט: … / big bar / percent + bytes / "אל תכבו את המחשב…". Also the cloner's and the class receiver's display |
| `#st-done` | `SCREEN_DONE` | open | הקליטה הושלמה / נכשלה + קליטה נוספת |
| `#st-room` | `SCREEN_ROOM` | gated | הפצה למחשבי שיכפול: `room.js` setup (image, target, who is awake, machine rows) and the live round (count, bar, hint, rows, stop-confirm) |
| `#st-class` | `SCREEN_CLASS` | gated, **open when a round is live** | הפצה לכיתות: `classes.js` step 1 (the classes) and the live round (joined, hint, rows, stop-confirm) |
| `#st-message` | `SCREEN_MESSAGE` | **open** | title + text: "המחשב אינו רשום", "חסר מזהה מכונה", … |

"Gated" = the login card is shown first. "Open" = reachable with no operator:
the message and the running task come from the `--state` file and take the
screen regardless of login (the `poll()` order of `station.js`), and a live
classroom round keeps `#st-class` visible without a session (#34) — its
actions then toast "החיבור פג — נדרשת כניסה מחדש." and do nothing, exactly as
`classes.js authed()` does on a 401.

## Toolkit: Pango + HarfBuzz + FriBidi + Cairo, straight onto KMS

**Chosen:** R09's primary candidate — Pango for text layout, drawing with
Cairo into a DRM/KMS dumb buffer (fbdev as fallback), plain C, no display
server. **Why, in one paragraph:** the only requirement that has already
eliminated toolkits is correct mixed Hebrew/Latin/digit text, and Pango is the
one small stack whose Hebrew is not a hope but a published dependency — its
README lists *"HarfBuzz for complex text shaping"* and *"fribidi for
bidirectional text handling"*, and Debian's `libpango-1.0-0` package literally
depends on `libfribidi0` ("Free Implementation of the Unicode BiDi algorithm")
and `libharfbuzz0b`. It is the text engine of GTK, i.e. of every Hebrew GNOME
desktop; it is not going to render `/dev/sda` inside a Hebrew sentence
differently from Chromium, which links the same two libraries. Size: the
`.deb`s for pango+pangocairo+harfbuzz+fribidi+cairo+pixman+freetype+
fontconfig+glib+libdrm sum to **≈ 3.9 MB compressed** (per-package figures in
`deps-debian.txt`, from packages.debian.org/trixie, 2026-09-09) before
transitive dependencies — measured properly by `tools/measure-size.sh`, not
estimated. And it is C, the language the project already builds
(`agent/fanout.c`), with gcc already in the initramfs build.

**Not chosen — Slint** (~3 MB single binary, bundles its own text stack). Its
Hebrew is unproven and the evidence points the wrong way: the `Text` element
documentation does not mention bidirectional text, RTL, or shaping at all
(docs.slint.dev, reference/elements/text, read 2026-09-09); the issue tracker
has **#7267 "Word order gets messed up and cursor location problem" — open**,
and #1317 asks for a text-direction property that LineEdit/TextEdit still lack.
R09 §2.1 says "supports UTF-8" is not a claim we accept; Slint today is at that
level for Hebrew. It also needs a Rust or C++ toolchain the project does not
have.

## How bidi and shaping are obtained (the R09 question)

| step | who does it | where it is visible |
|---|---|---|
| Unicode bidirectional algorithm (UAX#9): embedding levels, run order | **FriBidi** (`libfribidi0`) | Pango calls `fribidi_get_par_embedding_levels_ex()` in `pango/pango-bidi-type.c` (`pango_log2vis_fill_embedding_levels`, read from gitlab.gnome.org 2026-09-09). The smoke test also calls `fribidi_log2vis()` directly and prints the levels, so the claim is checkable without Pango. |
| Paragraph direction, itemisation into runs | **Pango** | `pango_context_set_base_dir(RTL)` + `pango_layout_set_auto_dir(TRUE)` = `<html dir="rtl">` with per-paragraph first-strong resolution, the browser's behaviour. `src/text.c`. |
| Shaping (Hebrew glyphs, marks, kerning) | **HarfBuzz** (`libharfbuzz0b`) | Pango ≥ 1.44 shapes exclusively through HarfBuzz; the smoke test prints the face each run was shaped with and fails on any `PANGO_GLYPH_UNKNOWN_FLAG` glyph (tofu). |
| Rasterisation | **Cairo + FreeType** | `pango_cairo_show_layout()` into an image surface, copied to the scanout. |

Nothing in `native-gui/` reorders characters or picks glyphs itself. Text
with mixed weight (the stop-confirm label's `<b>image name</b>`) and the
coloured status spans of the machine rows go through Pango **markup**, which
is the same layout path; values are escaped with `g_markup_escape_text`.

Two deliberate details of RTL that the browser handles and that were
reproduced on purpose: (1) `PANGO_ALIGN_LEFT` under auto-dir means *start*
(Pango swaps it for an RTL line — `text.c` says so) — that is CSS
`text-align:start`; (2) an `<input dir=rtl>` keeps its text at the physical
right edge even when the value is Latin, so fields use auto-dir **off** and
`PANGO_ALIGN_RIGHT` (`text_make_field`).

## The smoke test — run this first

```sh
cd native-gui
tools/run-smoke.sh            # builds and runs; exit 0 only when every check passes
```

`smoke/hebrew_bidi.c` renders three lines to `hebrew-bidi.png` **and asserts
the visual order from the layout itself** (principle 5: positive evidence,
not "no error"): `שלום Office 2024 — כיתה 12`, `הכונן /dev/sda במחשב
3C:52:82:A1:00:1F מוכן`, and — new with the round screens — the `#st-room`
subtitle `משדר: Office 2024 — סטנדרט · גל 2`: `משדר` at the far right, then
`Office 2024` as one LTR run, `סטנדרט`, `גל`, and the wave number at the far
left. It prints the FriBidi embedding levels and the
pango/harfbuzz/fribidi/cairo versions in use.

**If the smoke test fails, stop: that decides the toolkit.**

## What is here

```
native-gui/
  README.md            this file
  Makefile             make | make smoke | make png [STATE=file]
  deps-debian.txt      packages, versions, .deb sizes (cited)
  fonts.conf           minimal fontconfig for the initramfs (FONTCONFIG_FILE=)
  smoke/hebrew_bidi.c  the Hebrew bidi/shaping smoke test
  src/theme.{h,c}      colour tokens from console.css, + the 5 hard-coded CSS colours
  src/draw.{h,c}       rounded boxes, borders, the 160deg+radial background, box-shadow
  src/text.{h,c}       Pango: RTL layout, markup, fields, baseline, font presence check
  src/widgets.{h,c}    .btn / input / select+list / .sheet frame / .tray / .big-bar / .cls-bars / #toast,
                       fmtBytes and Progress.view ported
  src/ui.h             App state, the State the agent feeds, hit table, Screen/Mode enums
  src/screens.c        header, #st-login, #st-menu, the poll() routing rule, draw entry
  src/screens_capture.c  #st-pick, #st-progress, #st-done, #st-message
  src/screens_rounds.c   #st-room, #st-class (room.js / classes.js bodies)
  src/screens_tools.c    #649: the IT toolbox -- list / confirm / output views, and the tools + tool-result file pollers
  src/state.{h,c}      the --state file: key=value parser, mtime polling
  src/backend.{h,c}    DRM dumb buffer / fbdev / mem (a framebuffer file, #835), VT to graphics mode and back
  src/input.{h,c}      evdev keyboard (US map), mouse, touch; keyboards grabbed
  src/main.c           event loop, --auth-cmd, stdout records, --png renderer
  tools/run-smoke.sh   build + run the smoke test
  tools/measure-size.sh  the initramfs cost, measured the way build_initramfs.sh packs
```

## Contract with the agent (POSIX sh)

```
imagectl-station-gui --mac AA:.. --ip 10.. --auth-cmd 'imagectl-login' --state /run/imagectl/station.txt
```

* `--auth-cmd CMD` — on כניסה, CMD receives `<user>\n<pass>\n` on stdin; exit 0
  = signed in, first stdout line = role (`admin` shows the capture card,
  anything else hides it, as `station.js afterLogin` does). A curl to
  `/api/console/login` piped through `jq -r .role` fits here; it is not written
  because `agent/` is out of scope.
* `--state FILE` — the data `station.js` polls for, as `key=value` lines
  (below). Re-read every 2 s when its mtime or size changed; write it with
  `printf > tmp && mv tmp FILE`. Without it the screens have no disks,
  machines or task — they still render.
* `--screen progress` — start on `#st-progress` with no login (a cloning
  machine, a class receiver); the texts come from `title=` / `sub=` in the
  state file, else the HTML's defaults ("קולט…"). `--screen class` — start in
  the classes mode: with a live `session=` the round is shown read-only
  without login; without one, the login card (as `classes.js` does).
* `--signed-in USER [--role admin|deploy]` — start signed in at the menu, for
  an agent that restarts the GUI mid-flow.
* `--demo` accepts any non-empty user name (viewing only). **Without
  `--auth-cmd` every login fails** (closed by default). **Without `--mac`
  the message card "חסר מזהה מכונה" is shown**, as `index.html` does without
  `?mac=` — pass `--mac` (the `make png` and the recipe below do).
* exit 0 after `restore` (the agent takes the disk); exit 1: could not open a
  display, the fonts are missing, no input device, or stopped by a signal —
  with the reason on stderr.

### stdout: one record per operator action

The process **keeps running** across screens — the agent reads records from
its stdout (`| while read -r line`) and answers through the state file. A
record is a token line, zero or more `key=value` lines, and an **empty line**;
stdout is line-buffered so a record arrives as it happens.

| token | from | detail lines | meaning |
|---|---|---|---|
| `capture` | menu | — | the capture card was chosen; `#st-pick` is now showing (feed `disk=`/`folder=`) |
| `restore` | menu | — | the #382/#706 card: `#st-restore` is showing (feed `image=` from `allowed_images`, `machine_name=`) |
| `room` | menu | — | `#st-room` is showing (feed `machine=`/`image=`, then `round=`) |
| `direct` | menu | — | #715: the room screen **without** the image picker — the source is this machine's disk (feed `machine=`/`machine_drawers=`/`room_drawer=`, no `image=`) |
| `classes` | menu | — | `#st-class` is showing (feed `class=`, or `session=` for a live round) |
| `back` | חזרה on pick / room / class | — | back at the menu; stop feeding that screen |
| `again` | קליטה נוספת | — | back at the menu after `#st-done` |
| `capture-start` | התחל קליטה | `dev=`, `name=`, `desc=`, `folder=`, `folder_new=yes\|no` | POST `/api/console/tasks/capture` (`resolveFolder`: with `folder_new=yes` create the folder first). The screen stays on `#st-pick` until the file says `task=pending`/`running`; a failure goes back as `form_error=` |
| `room-open` | פתח סבב והער את החדר | `image=<id>`, `target=<n>` | POST `/api/console/room`; answer with `round=` or `room_error=` |
| `direct-open` | פתח סבב והער את המחשבים שנבחרו | `slots=<mac>@<port>,<port>/<mac>@<port>` | #715: POST `/api/console/room` with `source: {kind: build_disk, mac, disk}` + `target_slots`; the disk is the first non-removable one in the server inventory. Only ticked, present, fresh drawers are listed; none → `room_error=` locally |
| `room-wake` | העֵר … | — | POST `/api/console/room/wake`; answer with `toast=` |
| `room-start` | התחל עכשיו | — | POST `/api/console/room/start` |
| `room-close` | עצור סבב, second press | `confirm=<typed>` | POST `/api/console/room/close {confirm_name}`; the server decides (#533), answer with `room_error=` or drop `round=` |
| `class-pick` | a class card | `group=<id>` | the operator chose a class. **Steps 2–3 of the wizard (machine grid, image list) are not built** — see "Not built" |
| `class-start` | התחל עכשיו | — | POST `/api/console/sessions/<id>/start` |
| `class-close` | עצור סבב, second press | `confirm=<typed>` | POST `/api/console/sessions/<id>/close {confirm_name}` (#581) |
| `restore-start` | התחל שחזור | `image=<id>`, `confirm=<typed>` | #706/#1073: hand-off to the agent (`handoff` file) — the bridge re-reads the machine's registered name from `GET /api/v1/agent/state` and accepts only a `confirm` equal to it; the agent re-validates the image against fresh `allowed_images`, then `single_restore_run` erases the internal disk and reboots |
| `tool-list` | the "כלים" button of the build menu (#649) | — | the bridge writes `$GUI_DIR/tools` (`tools_gui_list` in `agent/lib/tools.sh`) |
| `tool-run\|<id>\|<arg>\|<confirm>` | הרץ on a tool's confirm view, or a `ro` row with no argument (#649) | — (the token carries the fields; `\|` and newlines never travel) | `tools_gui_run`: the framework refuses `rw`/`destroy` unless `confirm` equals the machine's registered name, runs the domain module, and writes `$GUI_DIR/tool-result` |

Local validation stays local, as in the HTML: "בחרו כונן ותנו שם לאימג'" and
"בחרו אימג' וקבעו יעד כוננים" are shown without a record.

### The `--state` file

Plain text, `key=value`, `|` between fields, one line per item; blank lines
and `#` comments ignored; an unknown key is reported on stderr and ignored. A
value must not contain `|` or a newline. **Every read replaces the whole
State**, so the file is always the complete picture (like one `/api/…`
response), not a diff.

```
# #st-pick (state.disks; removable ones are filtered out, as drawDisks does)
disk=<dev>|<model>|<size_bytes>|<has_data 0/1>|<removable 0/1>
folder=<name>                       # /api/console/folders, one per line

# #st-progress / #st-done (state.task)
task=pending|running|done|failed
task_name=<name>  task_disk=<dev>  task_error=<text>
task_direct=<0/1>                   # #715: 1 = a direct_send task (titles say "משדר", not "קולט")
pct=<0..100 | -1 unknown>  moving=<0/1>  bytes=<bytes_written>  partition=<n>
title=<text>  sub=<text>            # override the head texts (cloner / receiver display)

# forces #st-message (the !state.known / wrong-role cases)
message=<title>|<sub>

# #st-menu: the "הפצה לכיתות" card only when the edition flag AND the
# operator switch are on (#1081 classrooms + #880 class_deploy_enabled
# in hello /state). Missing = 0.
menu_class=<0/1>

# #1073: the machine's registered name (GET /api/v1/agent/state .name) -- the
# restore screen's typed confirmation. Always written; empty = no start.
machine_name=<name>

# #st-room (GET /api/console/room) and #st-class live rows
image=<id>|<name>|<folder>          # /api/console/images
machine=<name>|<mac>|<awake>|<joined>|<fresh_drawers>|<state>|<pct>|<moving>|<error>
round=<image_name>|<wave_number>|<open 0/1>|<written>|<target>|<ready>|<remaining>

# #st-class
class=<id>|<label>|<machines>       # /api/console/groups, role classroom
session=<image_name>|<prefix>|<group_label>|<open 0/1>|<joined>|<expected>|<starts_in_seconds>

form_error=  room_error=  class_error=   # the .error line of each card
toast=<text>                             # shown for 2.6 s on every reload that carries it
```

`machine=` rows serve both the room (`awake`/`fresh_drawers`/`joined`/`state`
per `room.js machineRows`, states `writing verifying waiting done failed
partial`) and a live class (`classes.js renderLive`, states `waiting done
failed` or a percentage). `pct` -1 with `moving` 1 is "נקראו בייטים · הסך לא
ידוע", -1 with 0 is "טרם נקראו בייטים · הסך לא ידוע" — `progress.js
Progress.view`, ported in `widgets.c`.

### #649: the toolbox files (`$GUI_DIR/tools`, `$GUI_DIR/tool-result`)

The IT toolbox is a fourth screen of the **build machine only** -- the
"כלים" button at the menu card's top-left (the RTL inline end), not one of
the choice cards; the cloner and class screens route before the menu and
never show it. Its data is not in the state file: two files **next to** it
(the directory of `--state` is `$GUI_DIR`), polled by mtime/size/inode like
the state file itself.

```
# $GUI_DIR/tools -- written by the bridge on tool-list
machine=<the name the operator must type for rw/destroy>
<id>|<domain>|<title>|<risk>|<args>      # one row per tool, tools_list order

# $GUI_DIR/tool-result -- unlinked at the start of a run, written at its end
<id>|<rc>|<path of the output text>|<seq>
```

* `domain` groups the list under דיסקים / רשת / Windows / אתחול / חומרה
  (`disk` / `net` / `windows` / `boot` / `hw`; an unknown domain is its own
  heading, named by the word). `risk` draws the tag: `ro` grey "קריאה
  בלבד", `rw` orange "משנה דיסק", `destroy` red "הרסני" -- and any other
  word is treated as `destroy`, as `tools.sh` does.
* `args` is the argument's label. **`disk:<label>`** makes the confirm view a
  disk picker over the `disk=` records (the chosen `dev` is sent); any other
  non-empty text is a free field (no spaces, no `|`). Empty = no argument.
* A `ro` tool with no argument runs on the click; everything else passes the
  confirm view -- the argument, and for `rw`/`destroy` the typed machine
  name (principle 7; the GUI compares locally to save a round trip, and the
  bridge compares again before any module runs).
* `rc` words: 0 הסתיים (success tokens) · 1 נמצאה בעיה · **2 לא הצלחנו
  לבדוק (warning tokens, never green -- principle 5)** · 3 האישור לא תואם ·
  4 כלי לא מוכר. `seq` increases per run so an identical result of a second
  run is still seen as new; a record for another tool, or with a seq already
  consumed, is ignored. While waiting, the output view shows "רץ…" with a
  spinner (the loop wakes every 120 ms only then).
* No wheel in `input.c`: the list and the output scroll with למעלה/למטה
  buttons in the footer, shown only when something is clipped.
* Empty list after the file was read: "לא נבחרו כלים בשרת (תשתית › ארגז כלים)" (#1050: the
  station offers only the selection saved in the console); before
  the bridge answered: "טוען את רשימת הכלים…" -- two states, not one.
* `--png` cards `tools`, `tools-confirm`, `tools-output` use a built-in
  sample list (`tools_sample`); a real `$GUI_DIR/tools` is read only when the
  live process runs with `--state`.

### Routing (the state machine)

`app_route()` in `screens.c` is `station.js poll()` in its order, run before
every draw:

1. `message=` present → `#st-message`.
2. `task=pending|running` (or `--screen progress`) → `#st-progress`; it
   remembers that it was watching.
3. Watching and `task=done|failed` → `#st-done` with `drawDone`'s texts, until
   קליטה נוספת.
4. Mode classes and (signed in or `session=`) → `#st-class`.
5. Not signed in → `#st-login`.
6. Mode room → `#st-room`; capture → `#st-pick`; tools → the toolbox (#649);
   else `#st-menu`.

The menu's four cards set the mode (capture → pick, room → room, classes →
class) and emit their word; `restore` exits. Restore has no confirm or pick
screen because the HTML has none — the #382 card hands the whole flow to the
agent, as before.

### `--backend mem`: a machine with no screen (#835)

When no display is attached (every connector down, so i915 creates no
`/dev/fb0` — cloner 2), the screens are drawn into a 1280×800 framebuffer
file that `imagectl-monitor --fb <file>` serves over RFB, so the console's
monitor page shows the machine anyway. `--backend mem [--fb-file PATH]`
(default `/run/imagectl/fb.mem`); `auto` falls back to it after drm and
fbdev. The kiosk (`agent/lib/guibridge.sh`) picks `mem` exactly when
`/dev/fb0` is not a character device, and `fbdev` otherwise, as before. The
file layout — a 4096-byte header with magic, geometry and a frame seqlock,
then XRGB8888 rows — is the contract in `docs/interfaces.md` §15.

### `--png`: every card, for comparing with the HTML

```sh
make png                      # out/station-<card>-{light,dark}.png, 20 files, sample data
make png STATE=/tmp/state.txt # the same cards from a real state file
```

Cards: `login menu pick progress done room room-live class class-live
cloner message restore direct`. The renderer puts the App into the state that *routes* to each
card and fails if the route landed elsewhere — so `make png` also exercises
the routing rule. The sample data has two disks (one chosen, the form open
with a name typed), two folders, two images, three cloner machines (one
writing at 42%, one done, one off), three classes (one with no machines,
drawn dim), a wave-2 round 7/24 and a class round 3/14 opening in 4:32.

### #828: the mockup is the design source now

Since #828 the look is `docs/design/native-gui-mockup-2026-09-13.html`
(the owner's 11 native screens), not the console CSS: every colour and
size in `theme.c` cites a `.native-*` rule (`css: selector | prop | index`)
or an inline style of one screen function (`html: nativeProgress |
font-size | 0`), and `tests/test_native_gui_theme.py` reads the mockup and
fails on any drift. Light is a derived palette pinned in the same test. What
the mockup shows that the state file cannot feed (SMART attributes per
disk, rate/ETA, image size/UEFI/SHA status, "דלג"/"בדוק שוב"/"אתחל
עכשיו"/"עצור כתיבה" buttons with no stdout record) is **not drawn** -- the
per-screen gap map is `docs/design/native-gui-redesign-status.md`. The
status bar's word ("ממתין לאימות", "כותב 3 דיסקים במקביל") is derived from
the screen itself, not a new state key. 1 CSS px = 1 pixel, as before.

### What matches the HTML, and where it does not

Every dimension, colour, radius, padding and text is taken from
`console.css`/`station.css`/`progress.css`/`index.html`/`station.js`/
`room.js`/`classes.js` and cited next to its use; the tokens are one table in
`theme.c`. Default theme is light; the header stays white/#8FA0B2 on the dark
gradient in both themes, as the CSS has it. Hover states, focus ring, the
password eye, `tray-multi`, the LED glow, `.disk-card.sel`, `.menu-card.dim`
and `.room-row.dim` (opacity .55), the three status colours (`room-ok`,
`room-bad #E5484D`, `room-warn #B36B00`), the big bar's three states and
`#toast` are all there.

1. **Theme button glyph.** `station.js` sets `☾`/`☀`; IBM Plex has neither
   code point and the initramfs carries no other font, so the button would
   show a tofu box. The moon and sun are drawn as vectors instead.
2. **`box-shadow` blur.** Cairo has no gaussian blur; shadows are 14 stacked
   translucent layers. Same colour, same offset, softer edge.
3. **Theme persistence.** The browser remembers the choice in localStorage;
   here it lives for the process (`--theme dark` to start dark).
4. **The restore card.** Three cards in `index.html`, four here (#382), same
   `.menu-card` numbers. Shown to the deploy role like room/classes; `show[]`
   in `screens.c` is the one line that flips it (`visible_cards` in `main.c`
   assumes only the first card is gated, so flip both together).
5. **The `<select>` list.** The browser's dropdown is a native widget outside
   the CSS; here it is a list in the page's language (surface, hair border,
   14px rows, indigo-soft for the chosen one) below the box, closed by a
   click elsewhere or Esc. The box itself, with its arrow at the inline end,
   follows the input rule the CSS gives `select`.
6. **`::placeholder`** has no rule in the CSS; the browser's default grey is
   approximated with the muted token.
7. **The indeterminate bar** is animated in the browser (`progress-unknown`);
   here the striped 40% block stands at its starting position.
8. **No scrolling.** `.sbody{overflow-y:auto}` — a body taller than 92vh is
   clipped, and what is clipped is not clickable. Eight disks or thirty
   machines fit a 1280×800 screen; a longer list would need a wheel, which
   `input.c` does not read.
9. **`<b>` is 700.** `.menu-card b` / `.disk-card b` / `.room-row b` /
   `.st-prog-line b` are `<b>` elements — `font-weight: bolder` of 400 is 700,
   which IBM Plex Sans Hebrew Bold provides. The menu previously used 600;
   fixed here.
10. **`.sub` is body text.** There is no `.sub` rule in `console.css` or
    `station.css` (only `.metric .sub`, which is not an ancestor on this
    page), so `<p class="sub">` renders as 14px ink in the browser. That is
    reproduced (`sub_make`, one place to change) — it is probably a missing
    CSS rule in the HTML, and that is a finding for its own Issue, not fixed
    here.
11. **Drawer chips.** `room.js drawerLine` renders a chip per drawer under a
    machine row ("מגירה 1 sdb · נכתבה"); the state file has no per-drawer
    data yet and the chips are not drawn.

Fonts are checked positively at start (`text_fonts_present`): if IBM Plex Sans
Hebrew or IBM Plex Mono do not resolve, the program exits 1 and says which,
instead of rendering in a fallback face nobody would notice was wrong.

### Not built, on purpose

* **`classes.js` steps 2 and 3** — the machine grid (`.machgrid`) and the
  image list grouped by folder (`.img-group` / `.img-row`) with the prefix
  field. The task scoped `#st-class` to the class list and the live view;
  `class-pick` is where the wizard would continue.
* **The student / off-network flows (#396)** — not in the HTML, not designed.
* **Hebrew typing.** `input.c` has a US keymap; an image name typed on the
  station is ASCII (a console account name and password already were). The
  agent can pre-fill nothing here — the name field is the operator's.

## Build recipe (Debian 13, the lab VM)

```sh
apt-get install -y --no-install-recommends gcc make pkg-config \
    libpango1.0-dev libcairo2-dev libdrm-dev libfribidi-dev libharfbuzz-dev
# fonts-ibm-plex is in contrib (build_initramfs.sh already handles that, #120)
apt-get install -y --no-install-recommends fonts-ibm-plex

cd native-gui
tools/run-smoke.sh           # 1. Hebrew first. Stop here if it fails.
make png                     # 2. out/station-*-{light,dark}.png: compare to the HTML
tools/measure-size.sh        # 3. the real initramfs cost, uncompressed and zstd'd
# 4. on a VM console (root, no compositor running):
FONTCONFIG_FILE=$PWD/fonts.conf ./imagectl-station-gui --demo --mac 00:15:5D:01:02:03 --state /tmp/state.txt
```

For the initramfs (a later change to `tools/build_initramfs.sh`, not made
here): `copy_bin imagectl-station-gui` pulls the library closure exactly as it
does for every other binary; add the six font files (the faces `index.html`
loads: Sans Hebrew 400/500/600/700, Mono 400/500 — `measure-size.sh` names
them) under `/usr/share/fonts/truetype/ibm-plex`, `fonts.conf` as
`/etc/imagectl/fonts.conf`, and export `FONTCONFIG_FILE` in the kiosk
wrapper. With that file, `/etc/fonts` and `/usr/share/fontconfig` need not
be packed. Kernel side: a KMS driver for the target must be in
`REQUIRED_MODULES` — `simpledrm` covers any UEFI machine from the firmware
framebuffer without a GPU driver, `hyperv_drm` the Gen2 lab VMs; both need
verifying. `cage`, `chromium`, `seatd`, `libinput`, `xkb-data` and the Mesa
DRI drivers all go away.

### Expected size — what is known and what is not

Known (packages.debian.org, trixie amd64, `.deb` = compressed = bytes over
the wire): the ten runtime libraries in `deps-debian.txt` total ≈ 3.9 MB, of
which glib is 1.5 MB. Not yet counted: their transitive dependencies
(`libthai0`/`libdatrie1` behind pango, `libpcre2-8-0`/`libffi8`/`libmount1`
behind glib, `libpng16-16`/`libbrotli`/`libbz2` behind freetype, and — from
memory, not from a fetched page — Debian's `libcairo2` is built with its X11
backends and so drags in `libx11-6`/`libxcb1`/`libxrender1`/`libxext6`
although nothing here opens a display). The six font files are a few hundred
kB each (not measured). **The honest number is the output of
`tools/measure-size.sh`**, which packs the closure the way the builder does
and zstd-compresses it. The expectation is single-digit MB against 120–315 MB
for a browser; the script will say.

## What I could NOT verify (written on Windows, without a C compiler)

* **The six new cards compile.** The login and menu did, on the VM; the
  files added since (`widgets.c`, `screens_capture.c`, `screens_rounds.c`,
  `state.c`, the new `main.c`) were written against the same APIs and
  re-read once. Expect the first `make` to surface typos.
* **They look like the HTML.** `make png` gives 20 files to put next to
  browser screenshots of each card (`room.js`/`classes.js` bodies need the
  server to render; the sample data mirrors their fields). The CSS margin
  rhythm around inputs inside labels (14 + 6, plus the inline-block
  baseline) and the `.folder-row` with the select's margin inside it were
  worked out by hand and may be a few pixels off.
* **Line C of the smoke test** passes — same mechanism as A and B, new
  assertion.
* **The state file loop** — atomic rewrite + mtime polling + the record stream
  on stdout — was not run against a real agent; `state_parse` was checked by
  reading, not by a test. A unit test in C for the parser is the obvious next
  addition.
* **KMS on the actual targets**, **input** (evdev, US keymap), and **sizes**
  — as before: not exercised on hardware.

## Next step

On the Debian 13 VM: `make` → fix what the compiler says → `tools/run-smoke.sh`
(line C included) → `make png` and compare the 20 PNGs to the HTML cards →
then wire the agent: `--auth-cmd`, the state file writer, and a reader for
the stdout records.
