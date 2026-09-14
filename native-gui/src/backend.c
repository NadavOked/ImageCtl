#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/fb.h>
#include <linux/kd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include "backend.h"

enum { KIND_DRM, KIND_FBDEV };

struct Backend {
    int kind, fd, w, h;
    uint32_t pitch;
    uint8_t *map; size_t map_len;
    cairo_surface_t *off;
    /* drm */
    uint32_t fb_id, handle, conn_id, crtc_id;
    drmModeModeInfo mode;
    drmModeCrtc *saved;
    /* vt */
    int tty_fd, tty_mode;                       /* KDGETMODE fills an int */
    /* fbdev flush */
    struct fb_var_screeninfo fbvar;             /* for FBIOPAN_DISPLAY */
};

/* ---- VT: keep the kernel console from drawing over us --------------------- */

static void vt_graphics(Backend *b) {
    b->tty_fd = open("/dev/tty0", O_RDWR | O_CLOEXEC);
    if (b->tty_fd < 0) { fprintf(stderr, "native-gui: /dev/tty0: %s (console may draw over the screen)\n", strerror(errno)); return; }
    if (ioctl(b->tty_fd, KDGETMODE, &b->tty_mode) < 0) b->tty_mode = KD_TEXT;
    if (ioctl(b->tty_fd, KDSETMODE, KD_GRAPHICS) < 0)
        fprintf(stderr, "native-gui: KDSETMODE KD_GRAPHICS: %s (console may draw over the screen)\n", strerror(errno));
}

static void vt_restore(Backend *b) {
    if (b->tty_fd < 0) return;
    ioctl(b->tty_fd, KDSETMODE, b->tty_mode);
    close(b->tty_fd);
    b->tty_fd = -1;
}

/* ---- DRM / KMS ----------------------------------------------------------------- */

static int drm_try(Backend *b, const char *path, char *err, size_t n) {
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) { snprintf(err, n, "%s: %s", path, strerror(errno)); return -1; }
    drmModeRes *res = drmModeGetResources(fd);
    if (!res) { snprintf(err, n, "%s: no KMS resources (%s)", path, strerror(errno)); close(fd); return -1; }

    drmModeConnector *conn = NULL;
    for (int i = 0; i < res->count_connectors && !conn; i++) {
        drmModeConnector *c = drmModeGetConnector(fd, res->connectors[i]);
        if (!c) continue;
        if (c->connection == DRM_MODE_CONNECTED && c->count_modes > 0) conn = c;
        else drmModeFreeConnector(c);
    }
    if (!conn) { snprintf(err, n, "%s: no connected display", path); drmModeFreeResources(res); close(fd); return -1; }

    uint32_t crtc = 0;
    if (conn->encoder_id) {
        drmModeEncoder *e = drmModeGetEncoder(fd, conn->encoder_id);
        if (e) { crtc = e->crtc_id; drmModeFreeEncoder(e); }
    }
    for (int i = 0; i < conn->count_encoders && !crtc; i++) {
        drmModeEncoder *e = drmModeGetEncoder(fd, conn->encoders[i]);
        if (!e) continue;
        for (int j = 0; j < res->count_crtcs; j++)
            if (e->possible_crtcs & (1u << j)) { crtc = res->crtcs[j]; break; }
        drmModeFreeEncoder(e);
    }
    if (!crtc) { snprintf(err, n, "%s: no CRTC for connector %u", path, conn->connector_id); drmModeFreeConnector(conn); drmModeFreeResources(res); close(fd); return -1; }

    b->mode = conn->modes[0];                       /* preferred mode is first */
    b->w = b->mode.hdisplay; b->h = b->mode.vdisplay;
    b->conn_id = conn->connector_id; b->crtc_id = crtc;
    drmModeFreeConnector(conn);
    drmModeFreeResources(res);

    struct drm_mode_create_dumb creq = { 0 };
    creq.width = b->w; creq.height = b->h; creq.bpp = 32;
    if (drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &creq) < 0) { snprintf(err, n, "%s: create dumb buffer: %s", path, strerror(errno)); close(fd); return -1; }
    b->pitch = creq.pitch; b->handle = creq.handle; b->map_len = creq.size;
    if (drmModeAddFB(fd, b->w, b->h, 24, 32, creq.pitch, creq.handle, &b->fb_id)) { snprintf(err, n, "%s: drmModeAddFB: %s", path, strerror(errno)); close(fd); return -1; }
    struct drm_mode_map_dumb mreq = { 0 };
    mreq.handle = creq.handle;
    if (drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &mreq) < 0) { snprintf(err, n, "%s: map dumb buffer: %s", path, strerror(errno)); close(fd); return -1; }
    b->map = mmap(NULL, b->map_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, mreq.offset);
    if (b->map == MAP_FAILED) { snprintf(err, n, "%s: mmap: %s", path, strerror(errno)); close(fd); return -1; }
    memset(b->map, 0, b->map_len);

    b->saved = drmModeGetCrtc(fd, crtc);
    if (drmModeSetCrtc(fd, crtc, b->fb_id, 0, 0, &b->conn_id, 1, &b->mode)) {
        snprintf(err, n, "%s: drmModeSetCrtc: %s (is another program DRM master?)", path, strerror(errno));
        munmap(b->map, b->map_len); close(fd); return -1;
    }
    b->fd = fd; b->kind = KIND_DRM;
    return 0;
}

static int drm_open(Backend *b, char *err, size_t n) {
    static const char *paths[] = { "/dev/dri/card0", "/dev/dri/card1", "/dev/dri/card2" };
    char why[3][160];
    for (int i = 0; i < 3; i++)
        if (drm_try(b, paths[i], why[i], sizeof why[i]) == 0) return 0;
    snprintf(err, n, "no usable KMS device: %s; %s; %s", why[0], why[1], why[2]);
    return -1;
}

/* ---- fbdev ------------------------------------------------------------------------- */

static int fb_open(Backend *b, char *err, size_t n) {
    const char *path = "/dev/fb0";
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) { snprintf(err, n, "%s: %s", path, strerror(errno)); return -1; }
    struct fb_var_screeninfo vi; struct fb_fix_screeninfo fi;
    if (ioctl(fd, FBIOGET_VSCREENINFO, &vi) < 0 || ioctl(fd, FBIOGET_FSCREENINFO, &fi) < 0) {
        snprintf(err, n, "%s: FBIOGET_*SCREENINFO: %s", path, strerror(errno)); close(fd); return -1;
    }
    /* cairo RGB24 is XRGB8888 little-endian; anything else is refused, not
     * mis-coloured (R09 s6: say so, do not show a wrong screen). */
    if (vi.bits_per_pixel != 32 || vi.red.offset != 16 || vi.green.offset != 8 || vi.blue.offset != 0) {
        snprintf(err, n, "%s: %ubpp with R@%u G@%u B@%u; only XRGB8888 is supported",
                 path, vi.bits_per_pixel, vi.red.offset, vi.green.offset, vi.blue.offset);
        close(fd); return -1;
    }
    b->map_len = fi.smem_len;
    b->map = mmap(NULL, b->map_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (b->map == MAP_FAILED) { snprintf(err, n, "%s: mmap: %s", path, strerror(errno)); close(fd); return -1; }
    b->w = vi.xres; b->h = vi.yres; b->pitch = fi.line_length;
    b->fbvar = vi;                              /* kept for the FBIOPAN_DISPLAY flush */
    b->fd = fd; b->kind = KIND_FBDEV;
    return 0;
}

/* ---- common ------------------------------------------------------------------------ */

Backend *backend_open(const char *kind, char *err, size_t n) {
    Backend *b = calloc(1, sizeof *b);
    b->fd = -1; b->tty_fd = -1;
    int ok = -1;
    char e1[512] = "", e2[512] = "";
    if (strcmp(kind, "drm") == 0)        ok = drm_open(b, err, n);
    else if (strcmp(kind, "fbdev") == 0) ok = fb_open(b, err, n);
    else if (strcmp(kind, "auto") == 0) {
        ok = drm_open(b, e1, sizeof e1);
        if (ok) ok = fb_open(b, e2, sizeof e2);
        if (ok) snprintf(err, n, "drm: %s / fbdev: %s", e1, e2);
    } else snprintf(err, n, "unknown backend '%s' (auto|drm|fbdev)", kind);
    if (ok) { free(b); return NULL; }

    b->off = cairo_image_surface_create(CAIRO_FORMAT_RGB24, b->w, b->h);
    if (cairo_surface_status(b->off) != CAIRO_STATUS_SUCCESS) {
        snprintf(err, n, "cairo: cannot create %dx%d surface", b->w, b->h);
        backend_close(b); return NULL;
    }
    vt_graphics(b);
    return b;
}

int backend_width(const Backend *b)  { return b->w; }
int backend_height(const Backend *b) { return b->h; }
cairo_surface_t *backend_surface(Backend *b) { return b->off; }

void backend_present(Backend *b) {
    cairo_surface_flush(b->off);
    const uint8_t *src = cairo_image_surface_get_data(b->off);
    int stride = cairo_image_surface_get_stride(b->off);
    size_t row = (size_t)b->w * 4;
    for (int y = 0; y < b->h; y++)
        memcpy(b->map + (size_t)y * b->pitch, src + (size_t)y * stride, row);
    /* On a modern i915 (Gen9+, e.g. the build laptop's Coffee Lake) the CPU's
     * writes to the mmap'd /dev/fb0 do not reach the scanout on their own: the
     * fb is write-combined and the display engine uses framebuffer compression,
     * so without a frontbuffer invalidate the panel keeps showing the last
     * frame (the build machine froze on a console frame while /dev/fb0 already
     * held the GUI). FBIOPAN_DISPLAY -> fb_pan_display calls the driver even at
     * yoffset 0 (kernel 6.12) and triggers intel_frontbuffer_invalidate -- that
     * is what makes the frame appear. An older i915 that scans /dev/fb0 out
     * directly (the IvyBridge cloner) does not need it, but the pan is cheap.
     * msync is kept only for a hypothetical driver whose fbdev is a
     * deferred-io shadow; on i915 there is no such shadow and it just returns
     * EINVAL, harmlessly. */
    if (b->kind == KIND_FBDEV) {
        msync(b->map, b->map_len, MS_SYNC);     /* no-op on i915; flushes a defio shadow elsewhere */
        ioctl(b->fd, FBIOPAN_DISPLAY, &b->fbvar);
    }
}

void backend_close(Backend *b) {
    if (!b) return;
    vt_restore(b);
    if (b->off) cairo_surface_destroy(b->off);
    if (b->map && b->map != MAP_FAILED) munmap(b->map, b->map_len);
    if (b->kind == KIND_DRM && b->fd >= 0) {
        if (b->saved) {
            drmModeSetCrtc(b->fd, b->saved->crtc_id, b->saved->buffer_id, b->saved->x, b->saved->y,
                           &b->conn_id, 1, &b->saved->mode);
            drmModeFreeCrtc(b->saved);
        }
        drmModeRmFB(b->fd, b->fb_id);
        struct drm_mode_destroy_dumb dreq = { 0 };
        dreq.handle = b->handle;
        drmIoctl(b->fd, DRM_IOCTL_MODE_DESTROY_DUMB, &dreq);
    }
    if (b->fd >= 0) close(b->fd);
    free(b);
}
