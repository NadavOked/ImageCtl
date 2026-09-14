/* Colour tokens -- copied one-to-one from server/static/console.css
 * (:root and :root[data-theme="dark"]) and the two fixed header colours
 * in server/static/station/station.css (.st-brand h1 / .st-brand p).
 * No colour is invented here; if a token changes in the CSS it changes
 * here, in the same place, under the same name. */
#ifndef IMAGECTL_THEME_H
#define IMAGECTL_THEME_H

typedef struct { double r, g, b; } Rgb;

typedef struct {
    const char *id;                 /* "light" | "dark" -- like data-theme */
    Rgb porcelain, surface, ink, muted, hair, indigo, indigo_soft;
    Rgb led_write, led_ok, led_idle, danger;
    Rgb hover, field, field_line, track, sunken;
    Rgb btn_hover, btn_hover_line, ink_hover, on_ink;
    Rgb danger_line, mark_line;
    Rgb login_a, login_b, login_glow;
    Rgb shadow_strong; double shadow_strong_a;
} Theme;

extern const Theme THEME_LIGHT;
extern const Theme THEME_DARK;

/* station.css: .st-brand h1{color:#fff} / .st-brand p, .st-id{color:#8FA0B2}
 * -- fixed in both themes because the header sits on the dark gradient. */
extern const Rgb HEAD_TITLE;
extern const Rgb HEAD_SUB;

/* The few colours the CSS hard-codes outside the token table:
 * station.css .room-bad{color:#E5484D} / .room-warn{color:#B36B00};
 * progress.css indeterminate stripes #4188af/#a2cfe5, unknown-idle #82939d. */
extern const Rgb ROOM_BAD;
extern const Rgb ROOM_WARN;
extern const Rgb STRIPE_A;
extern const Rgb STRIPE_B;
extern const Rgb STRIPE_IDLE;

/* design-tokens.css (פוליש vCenter מודרני): --r:9px; --r-sm:6px */
#define RADIUS_R     9.0
#define RADIUS_SM    6.0

#endif
