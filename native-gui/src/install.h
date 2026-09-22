#ifndef IMAGECTL_INSTALL_H
#define IMAGECTL_INSTALL_H

#include "ui.h"
#include "input.h"

typedef struct {
    const char *cmd;
    const char *cwd;
    double next_poll;
} InstallBridge;

/* Installer-only hit ids live outside ui.h so the station UI contract is unchanged. */
#define HIT_INSTALL_ADMIN_USER     1100
#define HIT_INSTALL_SHOW_PASSWORD  1101
#define HIT_INSTALL_SHOW_CONFIRM   1102
#define HIT_INSTALL_DISK_ACK       1103
#define HIT_INSTALL_BOOT_LOCAL     1104
#define HIT_INSTALL_TECHNICAL      1105
#define HIT_INSTALL_DISK_BASE      1120

void install_demo(App *a, int view);
int install_start(App *a, InstallBridge *bridge, int demo);
int install_handle(App *a, InstallBridge *bridge, const Event *event);
int install_tick(App *a, InstallBridge *bridge);
int install_polling(const App *a);
int install_password_ready(const InstallState *s);
const char *install_phase_label(const char *phase);
void screen_install(App *a, cairo_t *cr, double W, double H);
int install_render_png(App *a, const char *prefix, int w, int h);

#endif
