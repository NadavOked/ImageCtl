/* The last-resort screen -- see splash.h. The light theme (the default,
 * #1499), the ImageSrv symbol (#1302) and one line under it. */
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "splash.h"
#include "brand.h"
#include "text.h"
#include "theme.h"

#define SPLASH_CELL 14
#define SPLASH_PX 28
/* RLM first: the line starts with Latin, and auto-dir would make it an LTR
 * paragraph with the ellipsis on the wrong side of the Hebrew word. */
#define SPLASH_TEXT "\xE2\x80\x8F" "ImageSrv — מתאתחל…"

int splash_render_raw(const char *path, int w, int h) {
    const Theme *t = &THEME_LIGHT;
    cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, w, h);
    cairo_t *cr = cairo_create(sf);
    draw_station_background(cr, t, w, h);
    Text line = text_make(cr, FONT_SANS, SPLASH_PX, 500, SPLASH_TEXT, 0, DIR_RTL);
    const double sw = brand_symbol_width(SPLASH_CELL), sh = brand_symbol_height(SPLASH_CELL);
    const double gap = SPLASH_PX, top = (h - (sh + gap + line.h)) / 2;
    draw_brand_symbol(cr, (w - sw) / 2, top, SPLASH_CELL, t->indigo);
    text_draw(cr, &line, (w - line.w) / 2.0, top + sh + gap, t->ink);
    text_free(&line);
    cairo_destroy(cr);
    cairo_surface_flush(sf);

    int rc = cairo_surface_status(sf) != CAIRO_STATUS_SUCCESS;
    const unsigned char *px = cairo_image_surface_get_data(sf);
    const int stride = cairo_image_surface_get_stride(sf);
    FILE *f = rc ? NULL : fopen(path, "wb");
    if (!f) {
        fprintf(stderr, "native-gui: splash %s: %s\n", path, rc ? "cairo failed" : strerror(errno));
        cairo_surface_destroy(sf);
        return 1;
    }
    for (int y = 0; y < h && !rc; y++)
        rc = fwrite(px + (size_t)y * stride, 4, (size_t)w, f) != (size_t)w;
    if (fclose(f) != 0) rc = 1;
    if (rc) fprintf(stderr, "native-gui: splash %s: write failed\n", path);
    cairo_surface_destroy(sf);
    return rc;
}
