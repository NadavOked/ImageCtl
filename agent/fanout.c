/* fanout -- one stream in, several independent writers out.
 *
 * The cloning-room machines receive the multicast stream once and write it
 * to 2-3 drawers at the same time. The obvious `tee` cannot do this: it
 * writes to its outputs in lockstep, so the slowest drive dictates the pace
 * and a stalled one halts every drive on the machine. With 36 drives per
 * round that is not a rare case, it is a nightly one.
 *
 * So each target gets its own bounded queue and is written non-blocking.
 * A target that cannot keep up *while the others do* fills its queue and is
 * dropped -- reported as failed, by name, while the others carry on at full
 * speed. When every queue is full the stream itself is too fast for the
 * drives, and fanout blocks instead: that backpressure is what slows the
 * multicast sender down (#23).
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
#include <time.h>
#include <unistd.h>

/* Every wait in this program has a ceiling, and every ceiling is here.
 *
 *   open()ing a fifo        -> OPEN_RETRY_MS   (and O_NONBLOCK, so the call
 *                                               itself never blocks)
 *   room for the next block -> ROOM_GRACE_MS   (charged only to the drawer
 *                                               that is actually the holdup)
 *   nothing drains at all   -> DRAIN_STALL_MS  (both in make_room and in the
 *                                               final drain loop)
 *   poll() for writability  -> ROOM_POLL_MS / 200ms, never infinite
 *
 * The one wait without a ceiling of its own is read(stdin): a stream that
 * stops arriving is not something this program can distinguish from a stream
 * that is merely slow. Its ceiling lives one process up, in the shell --
 * wait_progress() in agent/lib/waits.sh watches the pv counter that feeds
 * this stdin and kills the pipeline when it stops moving. Named here so the
 * next reader does not have to go looking for it.
 */
#define MAX_TARGETS 8
#define READ_CHUNK (1024 * 1024)
#define OPEN_RETRY_MS 5000      /* how long to wait for a reader on a fifo */
#define ROOM_GRACE_MS 500       /* how long a full target may take to make room */
#define ROOM_POLL_MS 20
#define DRAIN_STALL_MS 30000    /* a target that takes nothing for this long is dead */
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
    long long held_ms;  /* accumulated ms it held the stream back while others were ready */
    long long taken;    /* bytes handed to this target's pipeline, for the counter */
    int cfd;            /* the counter file, or -1 when it could not be opened */
};

static struct target targets[MAX_TARGETS];
static int target_count;

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

/* Queue a block for one target. Returns 0 on success, -1 if it no longer
 * fits -- which is the moment the target is declared failed. */
static int enqueue(struct target *t, const char *data, size_t n)
{
    if (!t->alive)
        return 0;
    if (t->len + n > t->cap) {
        fail_target(t, "buffer overrun (drive too slow)");
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

/* Make sure every living target has room for the next block.
 *
 * The queue is there to absorb jitter, not to make a slow drive look fast.
 * But "too slow" is only meaningful *relative to the other drawers*, and the
 * measure is how long the stream waits *because of this target* -- not
 * whether its next block happens to fit. The stream waits because of one
 * target only when every other drawer is already ready: then it alone is
 * what the next block is missing. While two or more are short the stream is
 * simply faster than the drives -- a fast network, or a VM lab where the
 * wire is memory -- and the right move is to block: that backpressure
 * reaches udp-receiver and udpcast's flow control slows the sender (#23).
 *
 * And the debt is paid back at the rate it was charged. Equally slow drawers
 * take turns being the last one ready, a few milliseconds at a time; without
 * repayment that jitter integrates and every drawer but one is eventually
 * failed for being briefly out of phase with its neighbours (#45). A drawer
 * that really is the slow one is short far more often than not, so it still
 * reaches the grace and still fails, by name and in the open.
 *
 * A machine whose drives all stopped is still caught: nothing drains for
 * DRAIN_STALL_MS and it is failed, never waited on for ever. */
static void make_room(size_t n)
{
    long long now = now_ms();
    long long prev = now;
    long long last_drain = now;
    size_t prev_queued = (size_t)-1;
    for (;;) {
        int short_count = 0;              /* living targets the block does not fit */
        int living = 0;
        size_t queued = 0;
        for (int i = 0; i < target_count; i++) {
            struct target *t = &targets[i];
            flush_target(t);
            if (t->alive) {
                living++;
                queued += t->len;
                if (t->len + n > t->cap)
                    short_count++;
            }
        }
        if (queued < prev_queued)
            last_drain = now;             /* somebody took bytes */
        prev_queued = queued;

        long long delta = now - prev;
        int blocked = 0;
        for (int i = 0; i < target_count; i++) {
            struct target *t = &targets[i];
            if (!t->alive)
                continue;
            int too_full = t->len + n > t->cap;
            /* The stream is waiting on this drawer alone only if every other
             * living drawer is already ready. A lone survivor is never "the
             * holdup" -- there is nobody it could be holding up. */
            int holding_up = too_full && short_count == 1 && living > 1;
            if (holding_up) {
                t->held_ms += delta;
                if (t->held_ms >= ROOM_GRACE_MS) {
                    fail_target(t, "buffer overrun (drive too slow)");
                    continue;
                }
            } else if (t->held_ms > 0) {
                t->held_ms -= delta;      /* not the holdup: work the debt off */
                if (t->held_ms < 0)
                    t->held_ms = 0;
            }
            if (!too_full)
                continue;
            /* Not being charged for the wait must never mean waiting for
             * ever: a drawer nothing drains out of is failed regardless. */
            if (!holding_up && now - last_drain >= DRAIN_STALL_MS) {
                fail_target(t, "drawer stalled");
                continue;
            }
            blocked = 1;
        }
        if (!blocked)
            return;
        prev = now;
        wait_writable(ROOM_POLL_MS);
        now = now_ms();
    }
}

int main(int argc, char **argv)
{
    /* Writing to a pipe whose reader has gone raises SIGPIPE, and the default
     * action would kill this process outright -- taking down every healthy
     * drawer because one drive died. Ignoring it turns that into the EPIPE
     * that the write path already reports as a single failed target. */
    signal(SIGPIPE, SIG_IGN);

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
        t->held_ms = 0;
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
    }

    char *chunk = malloc(READ_CHUNK);
    if (!chunk) {
        fprintf(stderr, "fanout: out of memory\n");
        return 2;
    }

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
        make_room((size_t)got);
        for (int i = 0; i < target_count; i++) {
            enqueue(&targets[i], chunk, (size_t)got);
            flush_target(&targets[i]);
        }
        write_counters(0);
    }
    free(chunk);

    /* Drain what is still queued for the targets that survived. A slow drive
     * may take its time here -- nothing waits on it any more -- but one that
     * stops taking bytes altogether is failed rather than waited on forever. */
    size_t last_len[MAX_TARGETS];
    long long last_progress[MAX_TARGETS];
    for (int i = 0; i < target_count; i++) {
        last_len[i] = targets[i].len;
        last_progress[i] = now_ms();
    }
    while (alive_count() > 0) {
        int pending = 0;
        for (int i = 0; i < target_count; i++) {
            struct target *t = &targets[i];
            flush_target(t);
            if (!t->alive || t->len == 0)
                continue;
            pending = 1;
            if (t->len != last_len[i]) {
                last_len[i] = t->len;
                last_progress[i] = now_ms();
            } else if (now_ms() - last_progress[i] > DRAIN_STALL_MS) {
                fail_target(t, "stalled (no progress)");
            }
        }
        write_counters(0);
        if (!pending)
            break;
        wait_writable(200);
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
            close(t->fd);
            free(t->buf);
            printf("%s ok\n", t->path);
        } else {
            failed = 1;
            printf("%s failed %s\n", t->path, t->reason ? t->reason : "unknown");
        }
    }
    fflush(stdout);
    return failed ? 1 : 0;
}
