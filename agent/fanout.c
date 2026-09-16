/* fanout -- one stream in, several independent writers out.
 *
 * The cloning-room machines receive the multicast stream once and write it
 * to 2-3 drawers at the same time. The obvious `tee` cannot do this: it
 * writes to its outputs in lockstep, so the slowest drive dictates the pace
 * and a stalled one halts every drive on the machine. With 36 drives per
 * round that is not a rare case, it is a nightly one.
 *
 * So each target gets its own bounded queue and is written non-blocking.
 * When any live target's queue is full the stream is too fast for that drive,
 * and fanout blocks the reader until it drains: the stream is paced by the
 * slowest live drawer, and that backpressure is what slows the multicast
 * sender down (#23, #660). A slow drawer is never dropped -- the whole
 * machine follows it, the way udp-sender waits for a slow receiver rather
 * than evicting it (#657). Only a write error -- a reader that has gone,
 * EPIPE -- fails a single target, while the others carry on.
 *
 * The rule that shapes the whole program: never discard a block and let the
 * target continue. Multicast cannot resend, so a target that missed bytes can
 * only be a failed target. A drive that is quietly short a few megabytes in
 * the middle is far worse than one that visibly failed.
 *
 * Usage:  fanout <bytes-per-target-buffer> <out1> [out2] [out3] ...
 * Input:  stdin.
 * Output: one line per finished target on stdout: "<path> ok" | "<path> failed <reason>"
 *         plus, for each target, a running byte count appended to "<path>.bytes".
 * Exit:   0 if every target finished, 1 if any failed, 2 on usage/fatal errors.
 *
 * "ok" means the stream length was accepted -- empty stdin is never ok, and
 * FANOUT_EXPECTED_BYTES if set must match byte-for-byte -- and close() (and
 * fsync on a non-FIFO) succeeded. It is not a disk write confirmation: the
 * counter is bytes handed to the fd, and the shell's pipeline.rc is the
 * evidence that partclone exited 0 (#427).
 *
 * The byte count is here, and not in the shell, because this program is the
 * only place that knows how much of the stream each drawer actually took.
 * The single `pv` in front of it measures the machine's stream, which is the
 * same number for every drawer and therefore says nothing about any one of
 * them (#25). The format is one decimal number per line, appended -- exactly
 * what `pv -n` writes, so the agent's reporter reads both the same way
 * (progress.sh, interfaces.md section 4).
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* Every wait in this program has a ceiling, and every ceiling is here.
 *
 *   open()ing a fifo        -> OPEN_RETRY_MS   (and O_NONBLOCK, so the call
 *                                               itself never blocks)
 *   poll() for writability  -> ROOM_POLL_MS, never infinite
 *
 * What has no ceiling in the ordinary case, by design (#660): make_room()
 * blocks the reader for as long as a live drawer keeps draining, so a slow
 * drawer sets the pace and the stream follows it rather than being dropped.
 * The one exception (#674): make_room() evicts a drawer that is full AND has
 * made no write progress for STALL_EVICT_MS. A drawer that accepts any bytes
 * at all keeps resetting that clock, so healthy-but-slow drives are never
 * evicted -- only a consumer that has stopped taking bytes entirely, which a
 * hung disk (I/O timeout, not immediate EIO) is and a slow one is not. The
 * shell watchdog (wait_progress() in agent/lib/waits.sh) and partclone's exit
 * status remain the machine-level ceilings; this one isolates the responsible
 * drawer before they fire, so its healthy siblings are not taken down with it.
 */
#define MAX_TARGETS 8
#define READ_CHUNK (1024 * 1024)
#define OPEN_RETRY_MS 5000      /* how long to wait for a reader on a fifo */
#define ROOM_POLL_MS 20
#define STALL_EVICT_MS 150000   /* full drawer, no FIFO write progress; override for tests */
#define COUNTER_SUFFIX ".bytes" /* the per-target progress counter, next to the fifo */
#define COUNTER_MS 1000         /* how often it is refreshed (the report goes out every 2s) */

#define OPEN_NO_READER (-2)     /* the fifo is there, nobody ever opened it */

struct target {
    const char *path;
    int fd;
    char *buf;          /* ring buffer */
    size_t cap;
    size_t head;        /* next byte to write out */
    size_t len;         /* bytes queued */
    int alive;
    const char *reason;
    long long taken;    /* bytes handed to this target's pipeline, for the counter */
    long long last_progress_ms; /* monotonic ms of the last accepted FIFO write (#674) */
    int cfd;            /* the counter file, or -1 when it could not be opened */
};

static struct target targets[MAX_TARGETS];
static int target_count;
static long long stall_evict_ms = STALL_EVICT_MS;
static long long expected_bytes;          /* 0 = unknown; FANOUT_EXPECTED_BYTES */
static char stream_reason[96];            /* short/long stream, one string for all */

static long long now_ms(void);

/* Opening a fifo for writing fails with ENXIO until a reader arrives, and
 * the per-drawer pipelines are started moments earlier by the shell. Rather
 * than depend on that race, wait briefly for the reader to show up -- but
 * only briefly, and then say so: "no reader" and "no such path" are two
 * different faults for whoever reads the log, and running out of patience
 * has to be reported as running out of patience. */
static int open_output(const char *path)
{
    struct timespec pause = { 0, 20 * 1000 * 1000 };   /* 20ms */
    for (int waited = 0; waited < OPEN_RETRY_MS; waited += 20) {
        int fd = open(path, O_WRONLY | O_NONBLOCK);
        if (fd >= 0)
            return fd;
        if (errno != ENXIO)
            return -1;
        nanosleep(&pause, NULL);
    }
    return OPEN_NO_READER;
}

static void fail_target(struct target *t, const char *reason)
{
    if (!t->alive)
        return;
    t->alive = 0;
    t->reason = reason;
    if (t->fd >= 0) {
        close(t->fd);
        t->fd = -1;
    }
    free(t->buf);
    t->buf = NULL;
}

/* Queue a block for one target. make_room() has already guaranteed the room,
 * so a target that still does not fit is an internal invariant break, not a
 * slow drive -- said plainly rather than blamed on the disk (#660). */
static int enqueue(struct target *t, const char *data, size_t n)
{
    if (!t->alive)
        return 0;
    if (t->len + n > t->cap) {
        fail_target(t, "internal error (queue capacity invariant)");
        return -1;
    }
    size_t tail = (t->head + t->len) % t->cap;
    size_t first = t->cap - tail;
    if (first > n)
        first = n;
    memcpy(t->buf + tail, data, first);
    if (n > first)
        memcpy(t->buf, data + first, n - first);
    t->len += n;
    return 0;
}

/* Push as much as the pipe will take right now, without blocking. */
static void flush_target(struct target *t)
{
    while (t->alive && t->len > 0) {
        size_t run = t->cap - t->head;
        if (run > t->len)
            run = t->len;
        ssize_t written = write(t->fd, t->buf + t->head, run);
        if (written > 0) {
            t->head = (t->head + (size_t)written) % t->cap;
            t->len -= (size_t)written;
            t->taken += written;
            t->last_progress_ms = now_ms();
            continue;
        }
        if (written < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
            return;                       /* full for now; try again later */
        if (written < 0 && errno == EINTR)
            continue;
        fail_target(t, "write error");
        return;
    }
}

static int alive_count(void)
{
    int n = 0;
    for (int i = 0; i < target_count; i++)
        if (targets[i].alive)
            n++;
    return n;
}

/* Wait (briefly) until at least one queued target can accept more bytes. */
static void wait_writable(int timeout_ms)
{
    struct pollfd fds[MAX_TARGETS];
    int n = 0;
    for (int i = 0; i < target_count; i++) {
        if (targets[i].alive && targets[i].len > 0) {
            fds[n].fd = targets[i].fd;
            fds[n].events = POLLOUT;
            fds[n].revents = 0;
            n++;
        }
    }
    if (n > 0)
        poll(fds, (nfds_t)n, timeout_ms);
}

static long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Positive integer from the environment, or fallback. Junk, zero, and
 * overflow (strtoll saturates to LLONG_MAX, ERANGE) are refused out loud
 * and the fallback kept -- never taken on (principle 5, #674, #427). */
static long long env_ll(const char *name, long long fallback)
{
    const char *s = getenv(name);
    char *end;
    long long v;
    if (!s)
        return fallback;
    errno = 0;
    v = strtoll(s, &end, 10);
    if (end != s && *end == '\0' && v > 0 && errno != ERANGE)
        return v;
    fprintf(stderr, "fanout: ignoring invalid %s\n", name);
    return fallback;
}

/* Open "<path>.bytes" for the running count of one target. Truncated on
 * open, so every partition starts from zero -- the shell folds the finished
 * partition into the target's base before the next one begins. */
static int open_counter(const char *path)
{
    size_t size = strlen(path) + sizeof(COUNTER_SUFFIX);
    char *counter = malloc(size);
    int fd;
    if (!counter)
        return -1;
    snprintf(counter, size, "%s%s", path, COUNTER_SUFFIX);
    fd = open(counter, O_WRONLY | O_CREAT | O_TRUNC | O_APPEND, 0644);
    if (fd < 0)
        fprintf(stderr, "fanout: no progress counter for %s: %s\n",
                counter, strerror(errno));
    free(counter);
    return fd;
}

/* Append each target's count. A counter that cannot be written is closed and
 * said so once: the drawer itself is fine, and a drive is never failed over
 * a progress bar -- but the silence is not left unexplained either. */
static void write_counters(int force)
{
    static long long last_written;
    long long now = now_ms();
    if (!force && now - last_written < COUNTER_MS)
        return;
    last_written = now;
    for (int i = 0; i < target_count; i++) {
        struct target *t = &targets[i];
        char line[32];
        int n;
        if (t->cfd < 0)
            continue;
        n = snprintf(line, sizeof line, "%lld\n", t->taken);
        if (n > 0 && write(t->cfd, line, (size_t)n) != n) {
            fprintf(stderr, "fanout: progress counter for %s stopped: %s\n",
                    t->path, strerror(errno));
            close(t->cfd);
            t->cfd = -1;
        }
    }
}

/* Block the reader until every live target has room for the next n bytes.
 *
 * This is the whole back-pressure policy (#660): the stream advances only as
 * fast as the slowest live drawer that is still making progress, the way tee
 * paces to its slowest output -- but per-drawer and non-blocking, so a drawer
 * that has already write-errored is out and never waited on. Called with
 * n == cap at EOF, it drains every live ring to empty before the counts are
 * finalised.
 *
 * A drawer that is full AND has taken no bytes for stall_evict_ms is a hung
 * consumer, not a slow one, and is failed here so its siblings are not held
 * behind it forever (#674). Any accepted write in flush_target() refreshes
 * last_progress_ms, so a slow-but-draining drawer never trips this. */
static void make_room(size_t n)
{
    for (;;) {
        int blocked = 0;
        for (int i = 0; i < target_count; i++) {
            struct target *t = &targets[i];
            flush_target(t);
            if (!(t->alive && t->len > t->cap - n))
                continue;                 /* not live, or has room */
            long long idle = now_ms() - t->last_progress_ms;
            if (idle >= stall_evict_ms) {
                fprintf(stderr,
                        "fanout: %s stalled, no write progress for %lld ms -- evicting\n",
                        t->path, idle);
                fail_target(t, "stalled with no write progress");
                continue;                 /* out; do not wait on it */
            }
            blocked = 1;
        }
        write_counters(0);
        if (!blocked)
            return;
        wait_writable(ROOM_POLL_MS);
    }
}

int main(int argc, char **argv)
{
    /* Writing to a pipe whose reader has gone raises SIGPIPE, and the default
     * action would kill this process outright -- taking down every healthy
     * drawer because one drive died. Ignoring it turns that into the EPIPE
     * that the write path already reports as a single failed target. */
    signal(SIGPIPE, SIG_IGN);

    /* The stall ceiling is a production constant; the env var exists so the
     * negative-control tests can trip eviction in milliseconds instead of
     * minutes. FANOUT_EXPECTED_BYTES is the length check that separates an
     * empty stdin, a producer that died mid-stream, and a complete image --
     * without it those three share one EOF path and all look like ok (#427). */
    stall_evict_ms = env_ll("FANOUT_STALL_EVICT_MS", STALL_EVICT_MS);
    expected_bytes = env_ll("FANOUT_EXPECTED_BYTES", 0);

    if (argc < 3) {
        fprintf(stderr, "usage: fanout <buffer-bytes> <out> [out...]\n");
        return 2;
    }
    long long cap = atoll(argv[1]);
    if (cap < READ_CHUNK) {
        fprintf(stderr, "fanout: buffer must be at least %d bytes\n", READ_CHUNK);
        return 2;
    }
    target_count = argc - 2;
    if (target_count > MAX_TARGETS) {
        fprintf(stderr, "fanout: at most %d targets\n", MAX_TARGETS);
        return 2;
    }

    for (int i = 0; i < target_count; i++) {
        struct target *t = &targets[i];
        t->path = argv[i + 2];
        t->cap = (size_t)cap;
        t->buf = malloc(t->cap);
        t->head = t->len = 0;
        t->alive = 1;
        t->reason = NULL;
        t->taken = 0;
        t->cfd = open_counter(t->path);
        if (!t->buf) {
            fprintf(stderr, "fanout: out of memory\n");
            return 2;
        }
        int fd = open_output(t->path);
        if (fd < 0) {
            t->fd = -1;
            fail_target(t, fd == OPEN_NO_READER
                        ? "timed out waiting for the writer pipeline"
                        : "could not open");
            continue;
        }
        t->fd = fd;
        t->last_progress_ms = now_ms();   /* stall clock starts at open (#674) */
    }

    char *chunk = malloc(READ_CHUNK);
    if (!chunk) {
        fprintf(stderr, "fanout: out of memory\n");
        return 2;
    }

    long long stdin_got = 0;
    for (;;) {
        if (alive_count() == 0)
            break;                        /* nobody left to write to */
        ssize_t got = read(STDIN_FILENO, chunk, READ_CHUNK);
        if (got == 0)
            break;                        /* end of stream */
        if (got < 0) {
            if (errno == EINTR)
                continue;
            fprintf(stderr, "fanout: read error: %s\n", strerror(errno));
            free(chunk);
            return 2;
        }
        stdin_got += (long long)got;
        make_room((size_t)got);
        for (int i = 0; i < target_count; i++) {
            enqueue(&targets[i], chunk, (size_t)got);
            flush_target(&targets[i]);
        }
        write_counters(0);
    }
    free(chunk);

    /* EOF: drain every byte still queued, on a slow drawer too. A drawer that
     * has stopped taking bytes entirely for stall_evict_ms is failed by the
     * same health rule rather than blocking every sibling from finishing
     * (#674); a merely slow one keeps draining and is waited on to the end. */
    make_room((size_t)cap);

    /* Length before "ok": empty, short, and complete must not share a path.
     * Expected length from FANOUT_EXPECTED_BYTES (tests, and a future
     * per-file compressed size); without it, empty stdin is still failed
     * because that is one of the three states and needs no number (#427). */
    if (alive_count() > 0) {
        if (expected_bytes > 0 && stdin_got != expected_bytes) {
            snprintf(stream_reason, sizeof stream_reason,
                     "%s stream (got %lld of %lld)",
                     stdin_got < expected_bytes ? "short" : "long",
                     stdin_got, expected_bytes);
            for (int i = 0; i < target_count; i++)
                fail_target(&targets[i], stream_reason);
        } else if (expected_bytes == 0 && stdin_got == 0) {
            for (int i = 0; i < target_count; i++)
                fail_target(&targets[i], "empty stream");
        }
    }

    /* The last word on every counter is the exact total, not whatever the
     * one-second tick happened to catch. */
    write_counters(1);

    int failed = 0;
    for (int i = 0; i < target_count; i++) {
        struct target *t = &targets[i];
        if (t->cfd >= 0)
            close(t->cfd);
        if (t->alive) {
            /* fsync is for a real file; a FIFO returns EINVAL and is skipped.
             * close() errors were swallowed -- a target that lost its last
             * bytes at close still printed ok (#427). */
            struct stat st;
            if (t->fd >= 0 && fstat(t->fd, &st) == 0 && !S_ISFIFO(st.st_mode)) {
                if (fsync(t->fd) != 0) {
                    fail_target(t, "fsync failed");
                }
            }
        }
        if (t->alive && t->fd >= 0) {
            int fd = t->fd;
            t->fd = -1;
            if (close(fd) != 0) {
                t->alive = 0;
                t->reason = "close failed";
                free(t->buf);
                t->buf = NULL;
            } else {
                free(t->buf);
                t->buf = NULL;
            }
        }
        if (t->alive) {
            printf("%s ok\n", t->path);
        } else {
            failed = 1;
            printf("%s failed %s\n", t->path, t->reason ? t->reason : "unknown");
        }
    }
    fflush(stdout);
    return failed ? 1 : 0;
}
