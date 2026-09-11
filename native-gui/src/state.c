#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "state.h"

static char EMPTY[1] = "";

/* "a|b||d" -> f[0..3], missing fields are "" -- never NULL. */
static int split(char *v, char *f[], int max) {
    int n = 0;
    f[n++] = v;
    for (char *p = v; *p && n < max; p++)
        if (*p == '|') { *p = 0; f[n++] = p + 1; }
    for (int i = n; i < max; i++) f[i] = EMPTY;
    return n;
}

static void cp(char *dst, size_t n, const char *src) { snprintf(dst, n, "%s", src); }
static int  num(const char *s, int dflt) { return s[0] ? atoi(s) : dflt; }

void state_parse(State *s, FILE *fp) {
    memset(s, 0, sizeof *s);
    s->pct = -1;                                  /* no total known until said */
    char line[1024];
    while (fgets(line, sizeof line, fp)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!line[0] || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        const char *key = line;
        char *val = eq + 1, *f[9];
#define KEY(k) (strcmp(key, k) == 0)
        if (KEY("disk") && s->ndisks < MAX_DISKS) {
            split(val, f, 5);
            Disk *d = &s->disks[s->ndisks++];
            cp(d->dev, sizeof d->dev, f[0]); cp(d->model, sizeof d->model, f[1]);
            d->size_bytes = strtoull(f[2], NULL, 10);
            d->has_data = num(f[3], 0); d->removable = num(f[4], 0);
        } else if (KEY("folder") && s->nfolders < MAX_FOLDERS) {
            cp(s->folders[s->nfolders++], sizeof s->folders[0], val);
        } else if (KEY("image") && s->nimages < MAX_IMAGES) {
            split(val, f, 3);
            Image *im = &s->images[s->nimages++];
            cp(im->id, sizeof im->id, f[0]); cp(im->name, sizeof im->name, f[1]); cp(im->folder, sizeof im->folder, f[2]);
        } else if (KEY("machine") && s->nmachines < MAX_MACHINES) {
            split(val, f, 9);
            Machine *m = &s->machines[s->nmachines++];
            cp(m->name, sizeof m->name, f[0]); cp(m->mac, sizeof m->mac, f[1]);
            m->awake = num(f[2], 0); m->joined = num(f[3], 0); m->fresh_drawers = num(f[4], 0);
            cp(m->state, sizeof m->state, f[5]);
            m->pct = num(f[6], -1); m->moving = num(f[7], 0);
            cp(m->error, sizeof m->error, f[8]);
        } else if (KEY("class") && s->nclasses < MAX_CLASSES) {
            split(val, f, 3);
            ClassGroup *g = &s->classes[s->nclasses++];
            cp(g->id, sizeof g->id, f[0]); cp(g->label, sizeof g->label, f[1]); g->machines = num(f[2], 0);
        } else if (KEY("task")) {
            s->task = !strcmp(val, "pending") ? TASK_PENDING : !strcmp(val, "running") ? TASK_RUNNING
                    : !strcmp(val, "done")    ? TASK_DONE    : !strcmp(val, "failed")  ? TASK_FAILED : TASK_NONE;
        } else if (KEY("task_name"))  cp(s->task_name, sizeof s->task_name, val);
        else if (KEY("task_disk"))    cp(s->task_disk, sizeof s->task_disk, val);
        else if (KEY("task_error"))   cp(s->task_error, sizeof s->task_error, val);
        else if (KEY("pct"))          s->pct = num(val, -1);
        else if (KEY("moving"))       s->moving = num(val, 0);
        else if (KEY("partition"))    s->partition = num(val, 0);
        else if (KEY("bytes"))        s->bytes = strtoull(val, NULL, 10);
        else if (KEY("title"))        cp(s->title, sizeof s->title, val);
        else if (KEY("sub"))          cp(s->sub, sizeof s->sub, val);
        else if (KEY("message")) {
            split(val, f, 2);
            cp(s->msg_title, sizeof s->msg_title, f[0]); cp(s->msg_sub, sizeof s->msg_sub, f[1]);
        } else if (KEY("round")) {
            split(val, f, 7);
            s->has_round = 1;
            cp(s->round_image, sizeof s->round_image, f[0]);
            s->wave_number = num(f[1], 0); s->wave_open = num(f[2], 0);
            s->written = num(f[3], 0); s->target = num(f[4], 0);
            s->ready = num(f[5], 0); s->remaining = num(f[6], 0);
        } else if (KEY("session")) {
            split(val, f, 7);
            s->has_session = 1;
            cp(s->sess_image, sizeof s->sess_image, f[0]); cp(s->sess_prefix, sizeof s->sess_prefix, f[1]);
            cp(s->sess_group, sizeof s->sess_group, f[2]);
            s->sess_open = num(f[3], 0); s->joined = num(f[4], 0);
            s->expected = num(f[5], 0); s->starts_in = num(f[6], 0);
        } else if (KEY("form_error"))  cp(s->form_error, sizeof s->form_error, val);
        else if (KEY("room_error"))    cp(s->room_error, sizeof s->room_error, val);
        else if (KEY("class_error"))   cp(s->class_error, sizeof s->class_error, val);
        else if (KEY("toast"))         cp(s->toast, sizeof s->toast, val);
        else fprintf(stderr, "native-gui: state: unknown key '%s' ignored\n", key);
#undef KEY
    }
}

int state_poll(StateFile *f, State *s) {
    struct stat st;
    if (stat(f->path, &st) != 0) {
        if (!f->complained) { perror(f->path); f->complained = 1; }
        return -1;
    }
    f->complained = 0;
    if (f->loaded && st.st_mtime == f->mtime && st.st_size == f->size) return 0;
    FILE *fp = fopen(f->path, "r");
    if (!fp) { perror(f->path); return -1; }
    state_parse(s, fp);
    fclose(fp);
    f->mtime = st.st_mtime; f->size = st.st_size; f->loaded = 1;
    return 1;
}
