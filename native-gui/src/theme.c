#include "theme.h"

/* HEX(0xRRGGBB) -> Rgb in 0..1, usable in a static initialiser. */
#define HEX(v) { (((v) >> 16) & 255) / 255.0, (((v) >> 8) & 255) / 255.0, ((v) & 255) / 255.0 }

/* design-tokens.css :root (light) -- Clarity/vSphere restyle (#765) */
const Theme THEME_LIGHT = {
    .id = "light",
    .porcelain = HEX(0xEEF1F5), .surface = HEX(0xFFFFFF), .ink = HEX(0x313131),
    .muted = HEX(0x565656), .hair = HEX(0xCDCDCD), .indigo = HEX(0x0079B8),
    .indigo_soft = HEX(0xE8F4FA),
    .led_write = HEX(0xC25400), .led_ok = HEX(0x318700), .led_idle = HEX(0x737373),
    .danger = HEX(0xE12200),
    .hover = HEX(0xF2F2F2), .field = HEX(0xFFFFFF), .field_line = HEX(0xE8E8E8),
    .track = HEX(0xE8E8E8), .sunken = HEX(0xEEF1F5),
    .btn_hover = HEX(0xF3F9FC), .btn_hover_line = HEX(0x0079B8),
    .ink_hover = HEX(0x006394), .on_ink = HEX(0xFFFFFF),
    .danger_line = HEX(0xF0B8AE), .mark_line = HEX(0x9AADB8),
    .login_a = HEX(0x25333D), .login_b = HEX(0x17242D), .login_glow = HEX(0xFFFFFF),
    .shadow_strong = HEX(0x08101A), .shadow_strong_a = 0.55,   /* rgba(8,16,26,.55) */
};

/* design-tokens.css :root[data-theme="dark"] -- Clarity/vSphere restyle (#765).
   הערכים הם התוצאה המחושבת (resolved) של שרשראות ה-var(--clr-*) במצב כהה:
   token שה--clr-* שלו אינו נדרס בבלוק הכהה שומר על ערך האור (led-*, danger,
   on-ink, mark-line, login-*) — זה מה ש-console.css באמת מרנדר בכהה. */
const Theme THEME_DARK = {
    .id = "dark",
    .porcelain = HEX(0x151A1F), .surface = HEX(0x1C232A), .ink = HEX(0xE7EDF2),
    .muted = HEX(0xA9B4BD), .hair = HEX(0x35414B), .indigo = HEX(0x4C8FBD),
    .indigo_soft = HEX(0x223E50),
    .led_write = HEX(0xE1B34F), .led_ok = HEX(0x5FBD7A), .led_idle = HEX(0x737373),
    .danger = HEX(0xDC6B6B),
    .hover = HEX(0x25313A), .field = HEX(0x11181D), .field_line = HEX(0x303A42),
    .track = HEX(0x303A42), .sunken = HEX(0x151A1F),
    .btn_hover = HEX(0x202A31), .btn_hover_line = HEX(0x4C8FBD),
    .ink_hover = HEX(0x64A6D2), .on_ink = HEX(0xFFFFFF),
    .danger_line = HEX(0x5E3733), .mark_line = HEX(0x9AADB8),
    .login_a = HEX(0x25333D), .login_b = HEX(0x17242D), .login_glow = HEX(0xFFFFFF),
    .shadow_strong = HEX(0x000000), .shadow_strong_a = 0.65,   /* rgba(0,0,0,.65) */
};

const Rgb HEAD_TITLE = HEX(0xFFFFFF);
const Rgb HEAD_SUB   = HEX(0x8FA0B2);

const Rgb ROOM_BAD    = HEX(0xE5484D);
const Rgb ROOM_WARN   = HEX(0xB36B00);
const Rgb STRIPE_A    = HEX(0x4188AF);
const Rgb STRIPE_B    = HEX(0xA2CFE5);
const Rgb STRIPE_IDLE = HEX(0x82939D);
