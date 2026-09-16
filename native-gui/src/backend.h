/* Where pixels go. Two ways onto a screen with no display server, and one
 * for a machine with no screen at all:
 *   drm   -- KMS dumb buffer on /dev/dri/cardN (preferred; R09 Q1)
 *   fbdev -- /dev/fb0, XRGB8888 only (fallback when KMS is unavailable)
 *   mem   -- a 1280x800 framebuffer file (#835) that imagectl-monitor serves
 *            over RFB; docs/interfaces.md s15 has the layout
 * All draw into an offscreen cairo image and copy it to the scanout on
 * present(); the mapped scanout memory is uncached and slow to draw into
 * directly. There is no "png" backend: main.c renders straight to a cairo
 * image surface for --png. */
#ifndef IMAGECTL_BACKEND_H
#define IMAGECTL_BACKEND_H

#include <stddef.h>
#include <cairo.h>

typedef struct Backend Backend;

/* kind: "auto" (drm, then fbdev, then mem), "drm", "fbdev" or "mem".
 * mem_path: the file for "mem" (NULL = /run/imagectl/fb.mem). NULL + message
 * on failure -- the message names what was tried, never a silent blank. */
Backend *backend_open(const char *kind, const char *mem_path, char *err, size_t n);
int backend_width(const Backend *b);
int backend_height(const Backend *b);
cairo_surface_t *backend_surface(Backend *b);     /* offscreen RGB24 */
void backend_present(Backend *b);
/* Puts the display back as it was (saved CRTC / text-mode VT). */
void backend_close(Backend *b);

#endif
