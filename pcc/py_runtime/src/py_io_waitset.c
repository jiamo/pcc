/* py_io_waitset.c - scalable virtual-thread IO waitset (C mirror).
 *
 * Mirrors pcc/vthread/io_waitset_oracle.py exactly. See the header for the
 * abstraction, the two backends (poll fallback + Darwin kqueue), and the
 * semantics contract; see docs/design/pcc-vthread-oracles.md for the design
 * rationale (why a readiness-notifier replaces the O(n) per-poll rescan, and
 * why the fallback keeps level-triggered poll semantics).
 *
 * The poll-fallback structure is deliberately dependency-free (no PyObject, no
 * GC, no libpython, no <poll.h>): readiness is FED via set_ready, exactly like
 * the oracle's PollWaitSet stands in for poll(2)'s revents. That keeps it
 * deterministic and diffable against the oracle without live fds. The kqueue
 * backend (Darwin/BSD only) is the real syscall path over LIVE fds; on other
 * platforms it is unavailable and callers must use the poll fallback.
 */
#include "py_io_waitset.h"

#include <stdlib.h>
#include <string.h>

/* The real kqueue backend is compiled only where <sys/event.h> exists. */
#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__NetBSD__) \
    || defined(__OpenBSD__) || defined(__DragonFly__)
#define PCC_IO_WAITSET_HAVE_KQUEUE 1
#include <sys/event.h>
#include <sys/time.h>
#include <sys/types.h>
#include <errno.h>
#include <unistd.h>
#else
#define PCC_IO_WAITSET_HAVE_KQUEUE 0
#endif

#define PCC_IO_WAITSET_MIN_CAP 8

/* ---- slot table --------------------------------------------------------- */

static int64_t pcc_io_find_slot(const PccIoWaitSet *ws, int64_t fd) {
    for (int64_t i = 0; i < ws->len; i++) {
        if (ws->slots[i].state == 1 && ws->slots[i].fd == fd) return i;
    }
    return -1;
}

static int pcc_io_reserve_slot(PccIoWaitSet *ws) {
    if (ws->len < ws->cap) return 0;
    int64_t new_cap = ws->cap > 0 ? ws->cap * 2 : PCC_IO_WAITSET_MIN_CAP;
    PccIoWaitSlot *grown = (PccIoWaitSlot *)realloc(
        ws->slots, (size_t)new_cap * sizeof(PccIoWaitSlot)
    );
    if (grown == NULL) return -1;
    ws->slots = grown;
    ws->cap = new_cap;
    return 0;
}

/* Ensure the reusable output scratch can hold at least ``need`` entries each. */
static int pcc_io_reserve_output(PccIoWaitSet *ws, int64_t need) {
    if (need < 1) need = 1;
    if (ws->ready_cap < need) {
        PccIoReadyEvent *r = (PccIoReadyEvent *)realloc(
            ws->ready_buf, (size_t)need * sizeof(PccIoReadyEvent)
        );
        if (r == NULL) return -1;
        ws->ready_buf = r;
        ws->ready_cap = need;
    }
    if (ws->timeout_cap < need) {
        int64_t *t = (int64_t *)realloc(
            ws->timeout_buf, (size_t)need * sizeof(int64_t)
        );
        if (t == NULL) return -1;
        ws->timeout_buf = t;
        ws->timeout_cap = need;
    }
    return 0;
}

/* Compact the slot array by dropping empty slots (called after a wait drain so
 * live_count == len again and future scans stay tight). */
static void pcc_io_compact(PccIoWaitSet *ws) {
    int64_t w = 0;
    for (int64_t i = 0; i < ws->len; i++) {
        if (ws->slots[i].state == 1) {
            if (w != i) ws->slots[w] = ws->slots[i];
            w++;
        }
    }
    ws->len = w;
}

/* ---- lifecycle ---------------------------------------------------------- */

int pcc_io_waitset_init(PccIoWaitSet *ws, PccIoWaitSetBackend backend) {
    if (ws == NULL) return -1;
    memset(ws, 0, sizeof(*ws));
    ws->kq_fd = -1;
    ws->backend = backend;
    if (backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
#if PCC_IO_WAITSET_HAVE_KQUEUE
        int kq = kqueue();
        if (kq < 0) return -1;
        ws->kq_fd = kq;
#else
        /* Not available on this platform - caller must probe first and fall
         * back to the poll backend. */
        return -1;
#endif
    }
    return 0;
}

void pcc_io_waitset_dispose(PccIoWaitSet *ws) {
    if (ws == NULL) return;
#if PCC_IO_WAITSET_HAVE_KQUEUE
    if (ws->kq_fd >= 0) close(ws->kq_fd);
#endif
    free(ws->slots);
    free(ws->ready_buf);
    free(ws->timeout_buf);
    ws->slots = NULL;
    ws->ready_buf = NULL;
    ws->timeout_buf = NULL;
    ws->len = 0;
    ws->cap = 0;
    ws->live_count = 0;
    ws->ready_cap = 0;
    ws->timeout_cap = 0;
    ws->kq_fd = -1;
}

/* ---- kqueue filter (un)registration ------------------------------------- */

#if PCC_IO_WAITSET_HAVE_KQUEUE
/* Arm/disarm the EVFILT_READ / EVFILT_WRITE filters for one fd according to its
 * interest mask. On disarm we ignore ENOENT (filter was never armed). Returns 0
 * on success, -1 on a hard error. */
static int pcc_io_kq_update(PccIoWaitSet *ws, int64_t fd, int64_t interest, int add) {
    struct kevent changes[2];
    int nchg = 0;
    unsigned short flags = add ? (EV_ADD | EV_CLEAR) : EV_DELETE;
    /* EV_CLEAR gives edge-triggered semantics; we re-derive level behavior in
     * wait() by re-checking the reported readiness against interest, matching
     * the oracle's level-mode re-arm. */
    if (interest & PCC_IO_POLLIN) {
        EV_SET(&changes[nchg], (uintptr_t)fd, EVFILT_READ, flags, 0, 0, NULL);
        nchg++;
    }
    if (interest & PCC_IO_POLLOUT) {
        EV_SET(&changes[nchg], (uintptr_t)fd, EVFILT_WRITE, flags, 0, 0, NULL);
        nchg++;
    }
    if (nchg == 0) return 0;
    int rc = kevent(ws->kq_fd, changes, nchg, NULL, 0, NULL);
    if (rc < 0) {
        if (!add && errno == ENOENT) return 0;
        return -1;
    }
    return 0;
}
#endif

/* ---- add / remove ------------------------------------------------------- */

int pcc_io_waitset_add(
    PccIoWaitSet *ws,
    int64_t fd,
    int64_t interest,
    int64_t deadline,
    int edge
) {
    if (ws == NULL) return -1;
    int64_t idx = pcc_io_find_slot(ws, fd);
    if (idx < 0) {
        if (pcc_io_reserve_slot(ws) != 0) return -1;
        idx = ws->len++;
        ws->slots[idx].ready_mask = 0;
        ws->slots[idx].state = 1;
        ws->live_count++;
    } else {
#if PCC_IO_WAITSET_HAVE_KQUEUE
        if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
            /* Re-registration: drop old filters before re-arming the new mask. */
            (void)pcc_io_kq_update(ws, fd, ws->slots[idx].interest, 0);
        }
#endif
    }
    ws->slots[idx].fd = fd;
    ws->slots[idx].interest = interest;
    ws->slots[idx].deadline = deadline;
    ws->slots[idx].edge = edge ? 1 : 0;
#if PCC_IO_WAITSET_HAVE_KQUEUE
    if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
        if (pcc_io_kq_update(ws, fd, interest, 1) != 0) {
            /* Roll the slot back so the waitset stays consistent. */
            ws->slots[idx].state = 0;
            ws->live_count--;
            pcc_io_compact(ws);
            return -1;
        }
    }
#endif
    (void)edge;
    return 0;
}

int pcc_io_waitset_remove(PccIoWaitSet *ws, int64_t fd) {
    if (ws == NULL) return 0;
    int64_t idx = pcc_io_find_slot(ws, fd);
    if (idx < 0) return 0;
#if PCC_IO_WAITSET_HAVE_KQUEUE
    if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
        (void)pcc_io_kq_update(ws, fd, ws->slots[idx].interest, 0);
    }
#endif
    ws->slots[idx].state = 0;
    ws->live_count--;
    pcc_io_compact(ws);
    return 1;
}

int64_t pcc_io_waitset_count(const PccIoWaitSet *ws) {
    return ws == NULL ? 0 : ws->live_count;
}

void pcc_io_waitset_set_ready(PccIoWaitSet *ws, int64_t fd, int64_t events) {
    if (ws == NULL) return;
    int64_t idx = pcc_io_find_slot(ws, fd);
    if (idx < 0) return;
    ws->slots[idx].ready_mask |= events;
}

void pcc_io_waitset_clear_ready(PccIoWaitSet *ws, int64_t fd) {
    if (ws == NULL) return;
    int64_t idx = pcc_io_find_slot(ws, fd);
    if (idx < 0) return;
    ws->slots[idx].ready_mask = 0;
}

/* ---- wait: poll fallback ------------------------------------------------ */

/* Level-triggered rescan mirroring the oracle's PollWaitSet.wait and the C
 * ``while (*cur != NULL)`` loop: for each registered fd, if its fed readiness
 * (masked by interest | always-reported bits) is non-zero it is delivered as
 * ready; else if its deadline has passed it times out. Ready wins over timeout
 * at the same tick. Delivered/timed-out fds are unregistered (one-shot). */
static int pcc_io_wait_poll(PccIoWaitSet *ws, int64_t now, PccIoWaitResult *out) {
    if (pcc_io_reserve_output(ws, ws->live_count) != 0) return -1;
    int64_t nready = 0;
    int64_t ntimeout = 0;
    for (int64_t i = 0; i < ws->len; i++) {
        PccIoWaitSlot *slot = &ws->slots[i];
        if (slot->state != 1) continue;
        int64_t hit = slot->ready_mask
            & (slot->interest | PCC_IO_ALWAYS_REPORTED);
        int expired = slot->deadline >= 0 && slot->deadline <= now;
        if (hit != 0) {
            ws->ready_buf[nready].fd = slot->fd;
            ws->ready_buf[nready].events = hit;
            nready++;
            slot->state = 0;
            ws->live_count--;
        } else if (expired) {
            ws->timeout_buf[ntimeout++] = slot->fd;
            slot->state = 0;
            ws->live_count--;
        }
    }
    pcc_io_compact(ws);
    out->ready = ws->ready_buf;
    out->ready_len = nready;
    out->timed_out = ws->timeout_buf;
    out->timeout_len = ntimeout;
    return 0;
}

/* ---- wait: Darwin kqueue ------------------------------------------------ */

#if PCC_IO_WAITSET_HAVE_KQUEUE
static int pcc_io_wait_kqueue(PccIoWaitSet *ws, int64_t now, PccIoWaitResult *out) {
    if (pcc_io_reserve_output(ws, ws->live_count) != 0) return -1;

    struct timespec zero;
    zero.tv_sec = 0;
    zero.tv_nsec = 0;

    /* Collect up to live_count events (each fd contributes at most one relevant
     * ready delivery per wait). Bound by a small floor so a 0-registration wait
     * still passes a valid buffer. */
    int64_t evcap = ws->live_count > 0 ? ws->live_count : 1;
    /* An fd with both READ and WRITE interest can surface two kevents; size for
     * the worst case so nothing is dropped mid-drain. */
    struct kevent *evbuf = (struct kevent *)malloc(
        (size_t)(evcap * 2) * sizeof(struct kevent)
    );
    if (evbuf == NULL) return -1;

    int nev = kevent(ws->kq_fd, NULL, 0, evbuf, (int)(evcap * 2), &zero);
    if (nev < 0) {
        free(evbuf);
        if (errno == EINTR) {
            /* Treat as "no readiness this tick"; timeouts still processed. */
            nev = 0;
        } else {
            return -1;
        }
        evbuf = NULL;
    }

    int64_t nready = 0;
    /* Accumulate per-fd ready bits before delivering (an fd may report READ and
     * WRITE separately; the C poller reports the union masked by interest). */
    for (int i = 0; i < nev; i++) {
        struct kevent *ev = &evbuf[i];
        int64_t fd = (int64_t)ev->ident;
        int64_t idx = pcc_io_find_slot(ws, fd);
        if (idx < 0) continue; /* stale delivery for an already-removed fd */
        int64_t bits = 0;
        if (ev->filter == EVFILT_READ) bits |= PCC_IO_POLLIN;
        else if (ev->filter == EVFILT_WRITE) bits |= PCC_IO_POLLOUT;
        if (ev->flags & EV_EOF) bits |= PCC_IO_POLLHUP;
        if (ev->flags & EV_ERROR) bits |= PCC_IO_POLLERR;
        ws->slots[idx].ready_mask |= bits;
    }
    if (evbuf != NULL) free(evbuf);

    /* Deliver: mirror the poll fallback's interest-mask filtering and one-shot
     * removal so both backends agree on the readiness sequence (level mode). */
    for (int64_t i = 0; i < ws->len; i++) {
        PccIoWaitSlot *slot = &ws->slots[i];
        if (slot->state != 1) continue;
        int64_t hit = slot->ready_mask
            & (slot->interest | PCC_IO_ALWAYS_REPORTED);
        if (hit == 0) continue;
        ws->ready_buf[nready].fd = slot->fd;
        ws->ready_buf[nready].events = hit;
        nready++;
        (void)pcc_io_kq_update(ws, slot->fd, slot->interest, 0);
        slot->state = 0;
        ws->live_count--;
    }

    /* Timeouts: only fds with a finite deadline that has passed AND that did
     * not just deliver (ready wins over timeout at the same tick, enforced by
     * the state==1 check above having cleared delivered fds). */
    int64_t ntimeout = 0;
    for (int64_t i = 0; i < ws->len; i++) {
        PccIoWaitSlot *slot = &ws->slots[i];
        if (slot->state != 1) continue;
        if (slot->deadline >= 0 && slot->deadline <= now) {
            ws->timeout_buf[ntimeout++] = slot->fd;
            (void)pcc_io_kq_update(ws, slot->fd, slot->interest, 0);
            slot->state = 0;
            ws->live_count--;
        }
    }

    pcc_io_compact(ws);
    out->ready = ws->ready_buf;
    out->ready_len = nready;
    out->timed_out = ws->timeout_buf;
    out->timeout_len = ntimeout;
    return 0;
}
#endif

int pcc_io_waitset_wait(PccIoWaitSet *ws, int64_t now, PccIoWaitResult *out) {
    if (ws == NULL || out == NULL) return -1;
    out->ready = NULL;
    out->ready_len = 0;
    out->timed_out = NULL;
    out->timeout_len = 0;
    if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
#if PCC_IO_WAITSET_HAVE_KQUEUE
        return pcc_io_wait_kqueue(ws, now, out);
#else
        return -1;
#endif
    }
    return pcc_io_wait_poll(ws, now, out);
}

/* ---- platform capability + skip reason ---------------------------------- */

int pcc_io_waitset_kqueue_available(void) {
    return PCC_IO_WAITSET_HAVE_KQUEUE ? 1 : 0;
}

int pcc_io_waitset_real_kqueue_skip(PccIoWaitSetSkip *out) {
#if PCC_IO_WAITSET_HAVE_KQUEUE
    (void)out;
    return 0; /* kqueue available -> not skipped */
#else
    if (out != NULL) {
        out->path = "io_waitset.real_kqueue";
        out->reason =
            "real kqueue/kevent requires <sys/event.h> (Darwin/BSD); this "
            "platform has no kqueue backend. Use the poll fallback; a later "
            "runtime slice adds epoll_wait on Linux behind the same seam.";
    }
    return 1; /* skipped with reason */
#endif
}
