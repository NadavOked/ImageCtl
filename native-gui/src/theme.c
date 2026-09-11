#include "theme.h"

/* HEX(0xRRGGBB) -> Rgb in 0..1, usable in a static initialiser. */
#define HEX(v) { (((v) >> 16) & 255) / 255.0, (((v) >> 8) & 255) / 255.0, ((v) & 255) / 255.0 }

/* console.css :root (light) */
const Theme THEME_LIGHT = {
    .id = "light",
    .porcelain = HEX(0xEEF1F4), .surface = HEX(0xFFFFFF), .ink = HEX(0x15202B),
    .muted = HEX(0x6B7B8A), .hair = HEX(0xDCE2E8), .indigo = HEX(0x2B3FA0),
    .indigo_soft = HEX(0xEAECF8),
    .led_write = HEX(0xE39A2B), .led_ok = HEX(0x2E9E6B), .led_idle = HEX(0xB7C1CA),
    .danger = HEX(0xC0453B),
    .hover = HEX(0xF3F6F8), .field = HEX(0xFBFCFD), .field_line = HEX(0xEDF1F4),
    .track = HEX(0xE9EDF1), .sunken = HEX(0xFAFBFC),
    .btn_hover = HEX(0xF5F7F9), .btn_hover_line = HEX(0xC8D2DA),
    .ink_hover = HEX(0x22303F), .on_ink = HEX(0xFFFFFF),
    .danger_line = HEX(0xE7C6C3), .mark_line = HEX(0x5C6C7C),
    .login_a = HEX(0x1B2735), .login_b = HEX(0x111A24), .login_glow = HEX(0xFFFFFF),
    .shadow_strong = HEX(0x08101A), .shadow_strong_a = 0.55,   /* rgba(8,16,26,.55) */
};

/* console.css :root[data-theme="dark"] */
const Theme THEME_DARK = {
    .id = "dark",
    .porcelain = HEX(0x0F1620), .surface = HEX(0x18202B), .ink = HEX(0xE7EDF3),
    .muted = HEX(0x95A5B5), .hair = HEX(0x2A3644), .indigo = HEX(0x93A6F5),
    .indigo_soft = HEX(0x232D46),
    .led_write = HEX(0xE7A94A), .led_ok = HEX(0x46B784), .led_idle = HEX(0x4A5867),
    .danger = HEX(0xE4756B),
    .hover = HEX(0x1F2937), .field = HEX(0x131B25), .field_line = HEX(0x222D3A),
    .track = HEX(0x2A3644), .sunken = HEX(0x141C26),
    .btn_hover = HEX(0x222C39), .btn_hover_line = HEX(0x3A4757),
    .ink_hover = HEX(0xD3DCE6), .on_ink = HEX(0x0F1620),
    .danger_line = HEX(0x5E3733), .mark_line = HEX(0x5C6C7C),
    .login_a = HEX(0x141C26), .login_b = HEX(0x0B1119), .login_glow = HEX(0x26313F),
    .shadow_strong = HEX(0x000000), .shadow_strong_a = 0.65,   /* rgba(0,0,0,.65) */
};

const Rgb HEAD_TITLE = HEX(0xFFFFFF);
const Rgb HEAD_SUB   = HEX(0x8FA0B2);

const Rgb ROOM_BAD    = HEX(0xE5484D);
const Rgb ROOM_WARN   = HEX(0xB36B00);
const Rgb STRIPE_A    = HEX(0x4188AF);
const Rgb STRIPE_B    = HEX(0xA2CFE5);
const Rgb STRIPE_IDLE = HEX(0x82939D);
