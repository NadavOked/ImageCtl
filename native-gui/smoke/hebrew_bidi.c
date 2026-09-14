/* Hebrew bidi + shaping smoke test -- the one risk that decides the toolkit.
 *
 * Renders two mixed lines through the same stack the screens use
 * (Pango -> FriBidi for UAX#9, HarfBuzz for shaping, Cairo/FreeType for
 * pixels), writes a PNG for a human to look at, and -- because a picture
 * on a VM is not a test -- asserts the visual order from the layout
 * itself: which character landed where, in which direction, with which
 * font, and whether any glyph is the "unknown" box.
 *
 *   ./hebrew-bidi-smoke [out.png] [--any-font]
 *
 * Exit 0 only when every check passed. --any-font drops the requirement
 * that the face is IBM Plex Sans Hebrew (for a dev box without it); the
 * face that was used is still printed. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <cairo.h>
#include <pango/pangocairo.h>
#include <hb.h>
#include <fribidi.h>

#define FAMILY "IBM Plex Sans Hebrew"

static const char *LINE_A = "שלום Office 2024 — כיתה 12";
static const char *LINE_B = "הכונן /dev/sda במחשב 3C:52:82:A1:00:1F מוכן";
/* room.js: `משדר: ${round.image_name} · גל ${round.wave_number}` -- the
 * #st-room subtitle with a Latin image name, an em dash and a trailing
 * number, i.e. the mixed line the round screens draw every two seconds. */
static const char *LINE_C = "משדר: Office 2024 — סטנדרט · גל 2";

static int fails = 0, any_font = 0;

#define CHECK(cond, ...) do { int _c = (cond); printf("%s ", _c ? "ok  " : "FAIL"); \
                              printf(__VA_ARGS__); printf("\n"); if (!_c) fails++; } while (0)

/* Byte index of the first (or, with tail, the last) character of <needle>. */
static int index_of(const char *text, const char *needle, int tail) {
    const char *p = strstr(text, needle);
    if (!p) { fprintf(stderr, "internal: '%s' not in text\n", needle); exit(2); }
    int i = (int)(p - text);
    if (tail) {
        i += (int)strlen(needle) - 1;
        while (i > 0 && (text[i] & 0xC0) == 0x80) i--;   /* back to the char's lead byte */
    }
    return i;
}

/* Leading-edge x (pixels) of the character at byte index. For an RTL
 * character the leading edge is its right side, for LTR its left. */
static double x_at(PangoLayout *l, int idx) {
    PangoRectangle pos;
    pango_layout_index_to_pos(l, idx, &pos);
    return pos.x / (double)PANGO_SCALE;
}

static const char *dir_name(PangoDirection d) {
    return d == PANGO_DIRECTION_RTL || d == PANGO_DIRECTION_WEAK_RTL ? "RTL" : "LTR";
}

static PangoLayout *layout_for(cairo_t *cr, const char *text) {
    PangoLayout *l = pango_cairo_create_layout(cr);
    pango_context_set_base_dir(pango_layout_get_context(l), PANGO_DIRECTION_RTL);
    pango_layout_set_auto_dir(l, TRUE);
    pango_layout_context_changed(l);
    PangoFontDescription *d = pango_font_description_from_string(FAMILY);
    pango_font_description_set_absolute_size(d, 22 * PANGO_SCALE);
    pango_layout_set_font_description(l, d);
    pango_font_description_free(d);
    pango_layout_set_width(l, 860 * PANGO_SCALE);
    pango_layout_set_alignment(l, PANGO_ALIGN_LEFT);        /* = start under auto-dir */
    pango_layout_set_text(l, text, -1);
    return l;
}

/* Walk the runs: level, font, and whether any glyph is the notdef box. */
static void report_runs(PangoLayout *l, const char *text) {
    PangoLayoutIter *it = pango_layout_get_iter(l);
    int unknown = 0, runs = 0, wrong_face = 0;
    do {
        PangoLayoutRun *run = pango_layout_iter_get_run_readonly(it);
        if (!run) continue;
        PangoItem *item = run->item;
        for (int i = 0; i < run->glyphs->num_glyphs; i++)
            if (run->glyphs->glyphs[i].glyph & PANGO_GLYPH_UNKNOWN_FLAG) unknown++;
        PangoFontDescription *fd = pango_font_describe(item->analysis.font);
        const char *fam = pango_font_description_get_family(fd);
        if (!fam || strcmp(fam, FAMILY) != 0) wrong_face++;
        printf("    run @%-3d len %-3d level %d  %-6s font \"%s\"  \"%.*s\"\n",
               item->offset, item->length, item->analysis.level,
               item->analysis.level % 2 ? "RTL" : "LTR", fam ? fam : "?",
               item->length, text + item->offset);
        pango_font_description_free(fd);
        runs++;
    } while (pango_layout_iter_next_run(it));
    pango_layout_iter_free(it);
    CHECK(runs >= 3, "%d runs (mixed text splits into several)", runs);
    CHECK(unknown == 0, "%d glyph(s) missing from the font (tofu)", unknown);
    if (any_font) printf("info %d run(s) not in \"%s\" (--any-font: allowed)\n", wrong_face, FAMILY);
    else CHECK(wrong_face == 0, "every run shaped with \"%s\"", FAMILY);
}

/* What FriBidi alone says about the line: proves which library implements
 * UAX#9 here, independent of Pango's wrapping of it. */
static void fribidi_report(const char *s) {
    FriBidiChar u[256], v[256];
    FriBidiLevel lv[256];
    FriBidiStrIndex n = fribidi_charset_to_unicode(FRIBIDI_CHAR_SET_UTF8, s, (FriBidiStrIndex)strlen(s), u);
    FriBidiParType base = FRIBIDI_PAR_RTL;
    FriBidiLevel max = fribidi_log2vis(u, n, &base, v, NULL, NULL, lv);
    char out[1024];
    FriBidiStrIndex m = fribidi_unicode_to_charset(FRIBIDI_CHAR_SET_UTF8, v, n, out);
    out[m] = 0;
    printf("  fribidi_log2vis: levels");
    for (int i = 0; i < n; i++) printf(" %d", lv[i]);
    printf("\n  visual order (left to right): %s\n", out);
    /* log2vis returns max level + 1; an RTL paragraph with LTR runs inside
     * has levels 1 and 2, so 3. */
    CHECK(max == 3, "fribidi resolved levels 1 (Hebrew) and 2 (Latin/digits) in an RTL paragraph");
}

int main(int argc, char **argv) {
    const char *out = "hebrew-bidi.png";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--any-font")) any_font = 1; else out = argv[i];
    }
    printf("pango %s, harfbuzz %s, fribidi %s, cairo %s\n",
           pango_version_string(), hb_version_string(), fribidi_version_info, cairo_version_string());

    cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 900, 200);
    cairo_t *cr = cairo_create(sf);
    cairo_set_source_rgb(cr, 1, 1, 1); cairo_paint(cr);
    cairo_set_source_rgb(cr, 0.08, 0.13, 0.17);

    /* ---- line A: שלום Office 2024 — כיתה 12 -------------------------------- */
    printf("\nA: %s\n", LINE_A);
    PangoLayout *A = layout_for(cr, LINE_A);
    cairo_move_to(cr, 20, 30); pango_cairo_show_layout(cr, A);
    fribidi_report(LINE_A);
    report_runs(A, LINE_A);
    CHECK(!strcmp(dir_name(pango_layout_get_direction(A, 0)), "RTL"), "paragraph resolves RTL (first strong char is Hebrew)");
    CHECK(!strcmp(dir_name(pango_layout_get_direction(A, index_of(LINE_A, "Office", 0))), "LTR"), "\"Office\" is an LTR run");
    double xShalom = x_at(A, index_of(LINE_A, "שלום", 0));
    double xOffice = x_at(A, index_of(LINE_A, "Office", 0));
    double xKita   = x_at(A, index_of(LINE_A, "כיתה", 0));
    double x12     = x_at(A, index_of(LINE_A, "12", 0));
    CHECK(xShalom > xOffice, "שלום (%.0f) is right of Office (%.0f)", xShalom, xOffice);
    CHECK(xOffice > xKita,   "Office (%.0f) is right of כיתה (%.0f)", xOffice, xKita);
    CHECK(xKita > x12,       "כיתה (%.0f) is right of 12 (%.0f) -- the number ends the line at the left", xKita, x12);
    CHECK(x_at(A, index_of(LINE_A, "Office", 0)) < x_at(A, index_of(LINE_A, "Office", 1)), "inside Office: O left of e (LTR within the run)");
    CHECK(x_at(A, index_of(LINE_A, "שלום", 0)) > x_at(A, index_of(LINE_A, "שלום", 1)), "inside שלום: ש right of ם (RTL within the run)");
    CHECK(x_at(A, index_of(LINE_A, "2024", 0)) < x_at(A, index_of(LINE_A, "2024", 1)), "inside 2024: 2 left of 4 (digits keep LTR order)");
    int w, h; pango_layout_get_pixel_size(A, &w, &h);
    CHECK(w > 200 && w < 860, "single line, %d px wide", w);

    /* ---- line B: device name and MAC inside Hebrew --------------------------- */
    printf("\nB: %s\n", LINE_B);
    PangoLayout *B = layout_for(cr, LINE_B);
    cairo_move_to(cr, 20, 110); pango_cairo_show_layout(cr, B);
    fribidi_report(LINE_B);
    report_runs(B, LINE_B);
    double xKonen = x_at(B, index_of(LINE_B, "הכונן", 0));
    double xDev   = x_at(B, index_of(LINE_B, "dev/sda", 0));
    double xMach  = x_at(B, index_of(LINE_B, "במחשב", 0));
    double xMac   = x_at(B, index_of(LINE_B, "3C:52", 0));
    double xMuchan= x_at(B, index_of(LINE_B, "מוכן", 0));
    CHECK(xKonen > xDev && xDev > xMach && xMach > xMac && xMac > xMuchan,
          "visual order right->left: הכונן(%.0f) dev/sda(%.0f) במחשב(%.0f) MAC(%.0f) מוכן(%.0f)",
          xKonen, xDev, xMach, xMac, xMuchan);
    CHECK(x_at(B, index_of(LINE_B, "dev/sda", 0)) < x_at(B, index_of(LINE_B, "dev/sda", 1)), "inside dev/sda: d left of a");
    CHECK(x_at(B, index_of(LINE_B, "3C:52:82:A1:00:1F", 0)) < x_at(B, index_of(LINE_B, "3C:52:82:A1:00:1F", 1)), "inside the MAC: 3 left of F");
    CHECK(!strcmp(dir_name(pango_layout_get_direction(B, index_of(LINE_B, "3C", 0))), "LTR"), "the MAC is an LTR run");

    /* ---- line C: the room subtitle -- Hebrew, Latin name, dash, number ------ */
    printf("\nC: %s\n", LINE_C);
    PangoLayout *C = layout_for(cr, LINE_C);
    cairo_move_to(cr, 20, 160); pango_cairo_show_layout(cr, C);
    fribidi_report(LINE_C);
    report_runs(C, LINE_C);
    double xMeshader = x_at(C, index_of(LINE_C, "משדר", 0));
    double xOffice2  = x_at(C, index_of(LINE_C, "Office", 0));
    double xStd      = x_at(C, index_of(LINE_C, "סטנדרט", 0));
    double xGal      = x_at(C, index_of(LINE_C, "גל", 0));
    double xWave     = x_at(C, index_of(LINE_C, "גל 2", 1));
    CHECK(xMeshader > xOffice2 && xOffice2 > xStd && xStd > xGal && xGal > xWave,
          "visual order right->left: משדר(%.0f) Office(%.0f) סטנדרט(%.0f) גל(%.0f) 2(%.0f)",
          xMeshader, xOffice2, xStd, xGal, xWave);
    CHECK(x_at(C, index_of(LINE_C, "2024", 0)) < x_at(C, index_of(LINE_C, "2024", 1)), "inside 2024: 2 left of 4");
    CHECK(x_at(C, index_of(LINE_C, "Office", 0)) < x_at(C, index_of(LINE_C, "2024", 0)), "Office left of 2024 (one LTR run, in order)");

    cairo_status_t st = cairo_surface_write_to_png(sf, out);
    CHECK(st == CAIRO_STATUS_SUCCESS, "wrote %s (%s)", out, cairo_status_to_string(st));

    g_object_unref(A); g_object_unref(B); g_object_unref(C);
    cairo_destroy(cr); cairo_surface_destroy(sf);
    printf("\n%s: %d failure(s)\n", fails ? "FAIL" : "PASS", fails);
    return fails ? 1 : 0;
}
