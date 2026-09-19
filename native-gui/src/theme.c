#include "theme.h"

#define HEX(v) { (((v)>>16)&255)/255.0, (((v)>>8)&255)/255.0, ((v)&255)/255.0 }

/* Source: docs/design/native-gui-mockup-2026-09-18.html.
 * There is no native-only palette: every colour below is one of the console
 * tokens copied into that approved mockup (plus its --art-* / --bar-* tokens).
 * Repeated fields are compatibility names used by the older screen modules. */
const Theme THEME_LIGHT = {
 .id = "light",
 .porcelain = HEX(0xEEF1F5), .surface = HEX(0xFFFFFF),
 .ink = HEX(0x313131), .muted = HEX(0x565656), .hair = HEX(0xCDCDCD),
 .indigo = HEX(0x0079B8), .indigo_soft = HEX(0xE8F4FA),
 .led_write = HEX(0x0079B8), .led_ok = HEX(0x318700), .led_idle = HEX(0x8C8C8C),
 .danger = HEX(0xE12200), .hover = HEX(0xF2F2F2),
 .field = HEX(0xFFFFFF), .field_line = HEX(0xCDCDCD), .track = HEX(0xDCE5EB),
 .sunken = HEX(0xEEF1F5), .btn_hover = HEX(0xF2F2F2),
 .btn_hover_line = HEX(0x0079B8), .ink_hover = HEX(0x006394),
 .on_ink = HEX(0xFFFFFF), .danger_line = HEX(0xE12200),
 .mark_line = HEX(0x9AADB8), .login_a = HEX(0xE4E9EF),
 .login_b = HEX(0xCBD5DE), .login_glow = HEX(0xB4C3CF),
 .choice = HEX(0xFFFFFF), .choice_line = HEX(0xCDCDCD), .selected_line = HEX(0x0079B8),
 .button = HEX(0xFFFFFF), .button_line = HEX(0xCDCDCD),
 .danger_bg = HEX(0xFCEDEA), .warning_bg = HEX(0xFFF2E3),
 .warning_line = HEX(0xC25400), .warning_ink = HEX(0xC25400),
 .success_bg = HEX(0xEAF4E5), .success_line = HEX(0x318700), .success_ink = HEX(0x318700),
 .metric = HEX(0xFFFFFF), .metric_line = HEX(0xCDCDCD), .status_bg = HEX(0xFFFFFF),
 .header = HEX(0x25333D), .header_text = HEX(0xFFFFFF),
 .info = HEX(0x0079B8), .info_soft = HEX(0xE8F4FA),
 .success_soft = HEX(0xEAF4E5), .danger_soft = HEX(0xFCEDEA), .warning_soft = HEX(0xFFF2E3),
 .bar_done = HEX(0x318700), .bar_idle = HEX(0xB4C3CF),
 .art_bg = HEX(0xE4E9EF), .art_a = HEX(0xCBD5DE), .art_b = HEX(0xB4C3CF),
 .art_c = HEX(0x9AADB8), .art_line = HEX(0x7E93A1), .art_server = HEX(0x25333D),
 .disk_bg = HEX(0xFFFFFF), .disk_line = HEX(0xCDCDCD),
 .disk_selected = HEX(0xE8F4FA), .disk_selected_line = HEX(0x0079B8),
 .clone_line = HEX(0xCDCDCD), .round_line = HEX(0xCDCDCD),
 .image_bg = HEX(0xFFFFFF), .image_line = HEX(0xCDCDCD),
 .image_selected = HEX(0xE8F4FA), .image_selected_line = HEX(0x0079B8),
 .alert_bg = HEX(0xFFF2E3), .alert_line = HEX(0xC25400), .alert_ink = HEX(0x313131),
 .success_btn = HEX(0x318700), .success_btn_line = HEX(0x318700),
 .node_active_line = HEX(0x0079B8), .node_warn_line = HEX(0xC25400),
 .warn = HEX(0xC25400), .brand_accent = HEX(0xFFFFFF),
 .shadow_strong = HEX(0x25333D), .shadow_strong_a = 0.18,
 .radius = 4,
};

const Theme THEME_DARK = {
 .id = "dark",
 .porcelain = HEX(0x151A1F), /* css: :root[data-theme="dark"] | --clr-bg | 0 */
 .surface = HEX(0x1C232A), /* css: :root[data-theme="dark"] | --clr-surface | 0 */
 .ink = HEX(0xE7EDF2), /* css: :root[data-theme="dark"] | --clr-text | 0 */
 .muted = HEX(0xA9B4BD), /* css: :root[data-theme="dark"] | --clr-muted | 0 */
 .hair = HEX(0x35414B), /* css: :root[data-theme="dark"] | --clr-border | 0 */
 .indigo = HEX(0x4C8FBD), /* css: :root[data-theme="dark"] | --clr-action | 0 */
 .indigo_soft = HEX(0x223E50), /* css: :root[data-theme="dark"] | --clr-action-soft | 0 */
 .led_write = HEX(0x4C8FBD), /* css: :root[data-theme="dark"] | --bar-write | 0 */
 .led_ok = HEX(0x5FBD7A), /* css: :root[data-theme="dark"] | --clr-success | 0 */
 .led_idle = HEX(0x737373), /* css: :root[data-theme="dark"] | --clr-off | 0 */
 .danger = HEX(0xDC6B6B), /* css: :root[data-theme="dark"] | --clr-danger | 0 */
 .hover = HEX(0x25313A), /* css: :root[data-theme="dark"] | --clr-hover | 0 */
 .field = HEX(0x11181D), /* css: :root[data-theme="dark"] | --clr-field | 0 */
 .field_line = HEX(0x35414B), /* css: :root[data-theme="dark"] | --clr-border | 0 */
 .track = HEX(0x0E151A), /* css: :root[data-theme="dark"] | --bar-track | 0 */
 .sunken = HEX(0x151A1F), /* css: :root[data-theme="dark"] | --clr-bg | 0 */
 .btn_hover = HEX(0x25313A), /* css: :root[data-theme="dark"] | --clr-hover | 0 */
 .btn_hover_line = HEX(0x4C8FBD), /* css: :root[data-theme="dark"] | --clr-action | 0 */
 .ink_hover = HEX(0x64A6D2), /* css: :root[data-theme="dark"] | --clr-action-hover | 0 */
 .on_ink = HEX(0xFFFFFF), /* css: :root | --clr-header-text | 0 */
 .danger_line = HEX(0xDC6B6B), /* css: :root[data-theme="dark"] | --clr-danger | 0 */
 .mark_line = HEX(0x33434F), /* css: :root[data-theme="dark"] | --art-c | 0 */
 .login_a = HEX(0x11181D), /* css: :root[data-theme="dark"] | --art-bg | 0 */
 .login_b = HEX(0x1C262E), /* css: :root[data-theme="dark"] | --art-a | 0 */
 .login_glow = HEX(0x26333D), /* css: :root[data-theme="dark"] | --art-b | 0 */
 .choice = HEX(0x1C232A), /* css: .card | background | 0 */
 .choice_line = HEX(0x35414B), /* css: :root[data-theme="dark"] | --clr-border | 0 */
 .selected_line = HEX(0x4C8FBD), /* css: :root[data-theme="dark"] | --clr-action | 0 */
 .button = HEX(0x1C232A), /* css: :root[data-theme="dark"] | --clr-surface | 0 */
 .button_line = HEX(0x35414B), /* css: :root[data-theme="dark"] | --clr-border | 0 */
 .danger_bg = HEX(0x3A2323), /* css: :root[data-theme="dark"] | --clr-danger-soft | 0 */
 .warning_bg = HEX(0x33291A), /* css: :root[data-theme="dark"] | --clr-warning-soft | 0 */
 .warning_line = HEX(0xE1B34F), /* css: :root[data-theme="dark"] | --clr-warning | 0 */
 .warning_ink = HEX(0xE1B34F), /* css: :root[data-theme="dark"] | --clr-warning | 0 */
 .success_bg = HEX(0x1E3325), /* css: :root[data-theme="dark"] | --clr-success-soft | 0 */
 .success_line = HEX(0x5FBD7A), /* css: :root[data-theme="dark"] | --clr-success | 0 */
 .success_ink = HEX(0x5FBD7A), /* css: :root[data-theme="dark"] | --clr-success | 0 */
 .metric = HEX(0x1C232A), /* css: :root[data-theme="dark"] | --clr-surface | 0 */
 .metric_line = HEX(0x35414B), /* css: :root[data-theme="dark"] | --clr-border | 0 */
 .status_bg = HEX(0x182028), /* css: :root[data-theme="dark"] | --clr-nav | 0 */
 .header = HEX(0x26343F), /* css: :root[data-theme="dark"] | --clr-header | 0 */
 .header_text = HEX(0xFFFFFF), /* css: :root | --clr-header-text | 0 */
 .info = HEX(0x61B5B8), /* css: :root[data-theme="dark"] | --clr-info | 0 */
 .info_soft = HEX(0x1D3440), /* css: :root[data-theme="dark"] | --clr-info-soft | 0 */
 .success_soft = HEX(0x1E3325), /* css: :root[data-theme="dark"] | --clr-success-soft | 0 */
 .danger_soft = HEX(0x3A2323), /* css: :root[data-theme="dark"] | --clr-danger-soft | 0 */
 .warning_soft = HEX(0x33291A), /* css: :root[data-theme="dark"] | --clr-warning-soft | 0 */
 .bar_done = HEX(0x5FBD7A), /* css: :root[data-theme="dark"] | --bar-done | 0 */
 .bar_idle = HEX(0x33434F), /* css: :root[data-theme="dark"] | --bar-idle | 0 */
 .art_bg = HEX(0x11181D), /* css: :root[data-theme="dark"] | --art-bg | 0 */
 .art_a = HEX(0x1C262E), /* css: :root[data-theme="dark"] | --art-a | 0 */
 .art_b = HEX(0x26333D), /* css: :root[data-theme="dark"] | --art-b | 0 */
 .art_c = HEX(0x33434F), /* css: :root[data-theme="dark"] | --art-c | 0 */
 .art_line = HEX(0x4A5D6B), /* css: :root[data-theme="dark"] | --art-line | 0 */
 .art_server = HEX(0x70A9CD), /* css: :root[data-theme="dark"] | --art-server | 0 */
 .disk_bg = HEX(0x1C232A), /* css: :root[data-theme="dark"] | --clr-surface | 0 */
 .disk_line = HEX(0x35414B), /* css: :root[data-theme="dark"] | --clr-border | 0 */
 .disk_selected = HEX(0x223E50), /* css: :root[data-theme="dark"] | --clr-action-soft | 0 */
 .disk_selected_line = HEX(0x4C8FBD), /* css: :root[data-theme="dark"] | --clr-action | 0 */
 .clone_line = HEX(0x35414B), /* css: :root[data-theme="dark"] | --clr-border | 0 */
 .round_line = HEX(0x35414B), /* css: :root[data-theme="dark"] | --clr-border | 0 */
 .image_bg = HEX(0x11181D), /* css: :root[data-theme="dark"] | --clr-field | 0 */
 .image_line = HEX(0x35414B), /* css: :root[data-theme="dark"] | --clr-border | 0 */
 .image_selected = HEX(0x223E50), /* css: :root[data-theme="dark"] | --clr-action-soft | 0 */
 .image_selected_line = HEX(0x4C8FBD), /* css: :root[data-theme="dark"] | --clr-action | 0 */
 .alert_bg = HEX(0x33291A), /* css: .alert | background | 0 */
 .alert_line = HEX(0xE1B34F), /* css: .alert | border | 0 */
 .alert_ink = HEX(0xE7EDF2), /* css: .alert | color | 0 */
 .success_btn = HEX(0x5FBD7A), /* css: :root[data-theme="dark"] | --clr-success | 0 */
 .success_btn_line = HEX(0x5FBD7A), /* css: :root[data-theme="dark"] | --clr-success | 0 */
 .node_active_line = HEX(0x4C8FBD), /* css: .node.active | border-color | 0 */
 .node_warn_line = HEX(0xE1B34F), /* css: .node.warn | border-color | 0 */
 .warn = HEX(0xE1B34F), /* css: :root[data-theme="dark"] | --clr-warning | 0 */
 .brand_accent = HEX(0xFFFFFF), /* css: :root | --clr-header-text | 0 */
 .shadow_strong = HEX(0x26343F), /* css: :root[data-theme="dark"] | --clr-header | 0 */
 .shadow_strong_a = 0.45,
 .radius = 3,
};

/* Geometry bindings. The engine receives W/H; only the room column count,
 * cloner percentage size, and restore image columns branch on H/W. */
const double RADIUS_R = 3; /* css: :root[data-theme="dark"] | --r | 0 */
const double RADIUS_SM = 3; /* css: :root[data-theme="dark"] | --r | 0 */
const double N_PANEL_W = 600; /* css: .login | grid-template-columns | 1 */
const double N_NARROW_W = 420; /* css: .login | grid-template-columns | 0 */
const double N_PAD = 32; /* css: .body | padding | 1 */
const double N_CENTER_PAD = 32; /* css: .body | padding | 1 */
const double N_HEADER_H = 48; /* css: .hdr | height | 0 */
const double N_HEADER_PAD = 24; /* css: .hdr | padding | 1 */
const double N_STATUS_H = 36; /* css: .sb | height | 0 */
const double N_STATUS_PAD = 24; /* css: .sb | padding | 1 */
const double N_TITLE = 24; /* css: .ph h1 | font-size | 0 */
const double N_TITLE_GAP = 3; /* css: .ph p | margin-top | 0 */
const double N_SUB = 13; /* css: .ph p | font-size | 0 */
const double N_LABEL = 12; /* css: .f label | font-size | 0 */
const double N_LABEL_GAP = 2; /* css: .f label | margin-bottom | 0 */
const double N_FORM_GAP = 22; /* css: .f | margin-bottom | 0 */
const double N_FORM_TOP = 28; /* css: .lead | margin-bottom | 0 */
const double N_FIELD_H = 34; /* css: .f input | height | 0 */
const double N_FIELD_PAD = 4; /* css: .f input | padding | 2 */
const double N_BUTTON_H = 38; /* css: .btn | height | 0 */
const double N_BUTTON_PAD = 22; /* css: .btn | padding | 1 */
const double N_ACTION_GAP = 10; /* css: .actions | gap | 0 */
const double N_ACTION_TOP = 18; /* css: .body | gap | 0 */
const double N_CHOICE_GAP = 18; /* css: .cards | gap | 0 */
const double N_CHOICE_H = 52; /* css: .card .ic | height | 0 */
const double N_CHOICE_PAD = 26; /* css: .card | padding | 1 */
const double N_CHOICE_TITLE = 22; /* css: .card h2 | font-size | 0 */
const double N_CHOICE_SUB = 14; /* css: .card p | font-size | 0 */
const double N_CHOICE_ICON = 52; /* css: .card .ic | width | 0 */
const double N_LOGO = 72; /* css: .standby .mark | width | 0 */
const double N_LOGO_GAP = 22; /* css: .standby .mark | margin | 2 */
const double N_MESSAGE_ICON = 72; /* css: .standby .mark | height | 0 */
const double N_MESSAGE_GAP = 22; /* css: .standby .mark | margin | 2 */
const double N_DONE_ICON = 72; /* css: .standby .mark | width | 0 */
const double N_DONE_GAP = 22; /* css: .standby .mark | margin | 2 */
const double N_PROGRESS_PCT = 88; /* css: .dcard .big | font-size | 0 */
const double N_BAR_H = 6; /* css: .bar | height | 0 */
const double N_METRIC_PAD = 16; /* css: .stat | padding | 1 */
const double N_METRIC_LABEL = 12; /* css: .stat .k | font-size | 0 */
const double N_METRIC_VALUE = 24; /* css: .stat .v | font-size | 0 */
const double N_METRIC_GAP = 14; /* css: .strip | gap | 0 */
const double N_SHADOW_Y = 10; /* css: .alert | padding | 0 */
const double N_SHADOW_BLUR = 14; /* css: .alert | padding | 1 */
const double N_BORDER = 1; /* css: .card | border | 0 */
const double N_HEADER_FONT = 13; /* css: .hdr | font-size | 0 */
const double N_BRAND_FONT = 16; /* css: .hdr .brand | font-size | 0 */
const double N_IMAGE_PAD = 14; /* css: .img | padding | 1 */
const double N_IMAGE_GAP = 10; /* css: .imgs | gap | 0 */
const double N_IMAGE_TITLE = 15; /* css: .img b | font-size | 0 */
const double N_IMAGE_SUB = 12; /* css: .img small | font-size | 0 */
const double N_CLONE_GAP = 20; /* css: .mine | gap | 0 */
const double N_CLONE_PAD = 26; /* css: .dcard | padding | 1 */
const double N_CLONE_PCT = 88; /* css: .dcard .big | font-size | 0 */
const double N_CLONE_TITLE = 26; /* css: .dcard h2 | font-size | 0 */
const double N_ROOM_GAP = 14; /* css: .grid | gap | 0 */
const double N_ROOM_PAD = 14; /* css: .node | padding | 1 */
const double N_ROOM_MIN_H = 24; /* css: .pill | height | 0 */
const double N_CLASS_PRIMARY = 1.4; /* css: .restore | grid-template-columns | 0 */
const double N_CLASS_SECONDARY = 1; /* css: .restore | grid-template-columns | 1 */
const double N_ROUND_PAD = 22; /* css: .panel | padding | 1 */
const double N_BAR_SM = 6; /* css: .bar | height | 0 */
const double N_ROUND_ID = 24; /* css: .stat .v | font-size | 0 */
const double N_ROUND_META = 13; /* css: .ph p | font-size | 0 */
const double N_ALERT_PAD_Y = 10; /* css: .alert | padding | 0 */
const double N_ALERT_PAD_X = 14; /* css: .alert | padding | 1 */
const double N_ALERT_FONT = 13; /* css: .alert | font-size | 0 */
const double N_STATUS_DOT = 8; /* css: .sb .st i | width | 0 */
const double N_STATUS_GAP = 7; /* css: .sb .st | gap | 0 */
const double N_BRAND_GAP = 10; /* css: .hdr .brand | gap | 0 */
const double N_NODE_TITLE = 16; /* css: .node .nm b | font-size | 0 */
const double N_NODE_SUB = 12; /* css: .node .st | font-size | 0 */
const double N_NODE_DOT = 8; /* css: .node .nm i | width | 0 */
const double N_DISK_ICON_COL = 40; /* css: .tgt .ic | width | 0 */
const double N_DISK_PAD = 14; /* css: .img | padding | 1 */
const double N_DISK_GAP = 7; /* css: .disks | gap | 0 */
const double N_DISK_COL_GAP = 8; /* css: .disk | gap | 0 */
const double N_DISK_TITLE = 12; /* css: .disk | font-size | 0 */
const double N_DISK_SUB = 12; /* css: .disk | font-size | 0 */
const double N_DISK_SIDE = 12; /* css: .disk | font-size | 0 */
const double N_CLONE_SUB = 14; /* css: .dcard h2 small | font-size | 0 */
const double N_CLONE_STATUS = 12; /* css: .pill | font-size | 0 */
const double N_CLONE_STAT_PAD = 10; /* css: .dcard .kv | gap | 0 */
const double N_CLONE_STAT_LABEL = 13; /* css: .dcard .kv div | font-size | 0 */
const double N_CLONE_STAT_VALUE = 20; /* css: .dcard .kv div b | font-size | 0 */
const double N_CLONE_SOURCE_TITLE = 15; /* css: .img b | font-size | 0 */
const double N_PROGRESS_TITLE = 24; /* css: .ph h1 | font-size | 0 */
const double N_ROOM_TITLE = 24; /* css: .ph h1 | font-size | 0 */
const double N_CLONER_TITLE = 24; /* css: .ph h1 | font-size | 0 */
const double N_RESTORE_NAME = 15; /* css: .tgt b | font-size | 0 */
const double N_DIM_ALPHA = 0.6; /* css: .node.off | opacity | 0 */
