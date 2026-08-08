/* Host-C oracle for the production freestanding pcc-Python IO waitset.
 *
 * The production no-libpython archive owns these ABIs in
 * py/freestanding_io_waitset.py; this source remains an explicit host-C and
 * pcc-C oracle input.
 *
 * py_io_waitset.c - scalable virtual-thread IO waitset (C mirror).
 *
 * Mirrors pcc/vthread/io_waitset_oracle.py exactly. See the header for the
 * abstraction and its poll fallback, Darwin kqueue, and Linux epoll backends,
 * semantics contract; see docs/design/pcc-vthread-oracles.md for the design
 * rationale (why a readiness-notifier replaces the O(n) per-poll rescan, and
 * why the fallback keeps level-triggered poll semantics).
 *
 * The poll-fallback structure is deliberately dependency-free (no PyObject, no
 * GC, no libpython, no <poll.h>): readiness is FED via set_ready, exactly like
 * the oracle's PollWaitSet stands in for poll(2)'s revents. That keeps it
 * deterministic and diffable against the oracle without live fds. Kqueue and
 * epoll are real bounded syscall paths over LIVE fds; both use generation
 * tokens to reject stale delivery after an integer fd is reused.
 */
#include "py_io_waitset.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* The real kqueue backend is compiled only where <sys/event.h> exists. */
#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__NetBSD__) \
    || defined(__OpenBSD__) || defined(__DragonFly__)
#define PCC_IO_WAITSET_HAVE_KQUEUE 1
#include <sys/event.h>
#include <sys/time.h>
#include <sys/types.h>
#else
#define PCC_IO_WAITSET_HAVE_KQUEUE 0
#endif

#if defined(__linux__)
#define PCC_IO_WAITSET_HAVE_EPOLL 1
#include <sys/epoll.h>
#include <sys/eventfd.h>
#else
#define PCC_IO_WAITSET_HAVE_EPOLL 0
#endif

#define PCC_IO_WAITSET_MIN_CAP 8
#define PCC_IO_WAITSET_WAKE_IDENT 1
#define PCC_IO_WAITSET_WAKE_TOKEN UINT64_C(0)

/* ---- slot table --------------------------------------------------------- */

static int64_t pcc_io_find_slot(const PccIoWaitSet *ws, int64_t fd) {
    for (int64_t i = 0; i < ws->len; i++) {
        if (ws->slots[i].state == 1 && ws->slots[i].fd == fd) return i;
    }
    return -1;
}

static int64_t pcc_io_find_slot_generation(
    const PccIoWaitSet *ws,
    int64_t fd,
    uint32_t generation
) {
    int64_t idx = pcc_io_find_slot(ws, fd);
    if (idx < 0 || ws->slots[idx].generation != generation) return -1;
    return idx;
}

static uint32_t pcc_io_next_generation(PccIoWaitSet *ws) {
    if (ws->next_generation == 0 || ws->next_generation >= 0x7fffffffU) {
        ws->next_generation = 1;
    } else {
        ws->next_generation++;
    }
    return ws->next_generation;
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

#if PCC_IO_WAITSET_HAVE_EPOLL
static uint64_t pcc_io_epoll_token(const PccIoWaitSlot *slot) {
    return ((uint64_t)slot->generation << 32) | (uint32_t)slot->fd;
}

static int pcc_io_epoll_update(
    PccIoWaitSet *ws,
    const PccIoWaitSlot *slot,
    int operation
) {
    struct epoll_event event;
    memset(&event, 0, sizeof(event));
    if (slot->interest & PCC_IO_POLLIN) event.events |= EPOLLIN;
    if (slot->interest & PCC_IO_POLLOUT) event.events |= EPOLLOUT;
    event.events |= EPOLLONESHOT;
    if (slot->edge) event.events |= EPOLLET;
    event.data.u64 = pcc_io_epoll_token(slot);
    int rc = epoll_ctl(ws->kq_fd, operation, (int)slot->fd, &event);
    if (rc == 0) return 0;
    if (operation == EPOLL_CTL_DEL && (errno == ENOENT || errno == EBADF)) {
        return 0;
    }
    if (operation == EPOLL_CTL_ADD && errno == EEXIST) {
        return epoll_ctl(ws->kq_fd, EPOLL_CTL_MOD, (int)slot->fd, &event);
    }
    return -1;
}
#endif

/* ---- lifecycle ---------------------------------------------------------- */

int pcc_io_waitset_init(PccIoWaitSet *ws, PccIoWaitSetBackend backend) {
    if (ws == NULL) return -1;
    memset(ws, 0, sizeof(*ws));
    ws->kq_fd = -1;
    ws->wake_fd = -1;
    ws->backend = backend;
    if (
        backend != PCC_IO_WAITSET_BACKEND_POLL
        && backend != PCC_IO_WAITSET_BACKEND_KQUEUE
        && backend != PCC_IO_WAITSET_BACKEND_EPOLL
    ) return -1;
    if (backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
#if PCC_IO_WAITSET_HAVE_KQUEUE
        int kq = kqueue();
        if (kq < 0) return -1;
        ws->kq_fd = kq;
        struct kevent wake_event;
        EV_SET(
            &wake_event,
            PCC_IO_WAITSET_WAKE_IDENT,
            EVFILT_USER,
            EV_ADD | EV_CLEAR,
            0,
            0,
            NULL
        );
        if (kevent(kq, &wake_event, 1, NULL, 0, NULL) < 0) {
            close(kq);
            ws->kq_fd = -1;
            return -1;
        }
#else
        /* Not available on this platform - caller must probe first and fall
         * back to the poll backend. */
        return -1;
#endif
    }
    if (backend == PCC_IO_WAITSET_BACKEND_EPOLL) {
#if PCC_IO_WAITSET_HAVE_EPOLL
        int epfd = epoll_create1(EPOLL_CLOEXEC);
        if (epfd < 0) return -1;
        int wake = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
        if (wake < 0) {
            close(epfd);
            return -1;
        }
        struct epoll_event wake_event;
        memset(&wake_event, 0, sizeof(wake_event));
        wake_event.events = EPOLLIN;
        wake_event.data.u64 = PCC_IO_WAITSET_WAKE_TOKEN;
        if (epoll_ctl(epfd, EPOLL_CTL_ADD, wake, &wake_event) != 0) {
            close(wake);
            close(epfd);
            return -1;
        }
        ws->kq_fd = epfd;
        ws->wake_fd = wake;
#else
        return -1;
#endif
    }
    return 0;
}

void pcc_io_waitset_dispose(PccIoWaitSet *ws) {
    if (ws == NULL) return;
    if (ws->wake_fd >= 0) close(ws->wake_fd);
    if (ws->kq_fd >= 0) close(ws->kq_fd);
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
    ws->wake_fd = -1;
    ws->next_generation = 0;
    ws->backend = PCC_IO_WAITSET_BACKEND_POLL;
}

int pcc_io_waitset_interrupt(PccIoWaitSet *ws) {
    if (ws == NULL) return -1;
    if (ws->backend == PCC_IO_WAITSET_BACKEND_POLL) return 0;
#if PCC_IO_WAITSET_HAVE_KQUEUE
    if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
        struct kevent trigger;
        EV_SET(
            &trigger,
            PCC_IO_WAITSET_WAKE_IDENT,
            EVFILT_USER,
            0,
            NOTE_TRIGGER,
            0,
            NULL
        );
        for (;;) {
            if (kevent(ws->kq_fd, &trigger, 1, NULL, 0, NULL) == 0) return 0;
            if (errno != EINTR) return -1;
        }
    }
#endif
#if PCC_IO_WAITSET_HAVE_EPOLL
    if (ws->backend == PCC_IO_WAITSET_BACKEND_EPOLL) {
        uint64_t one = 1;
        for (;;) {
            ssize_t written = write(ws->wake_fd, &one, sizeof(one));
            if (written == (ssize_t)sizeof(one)) return 0;
            if (written < 0 && errno == EINTR) continue;
            /* A full nonblocking eventfd is already a pending interrupt. */
            if (written < 0 && errno == EAGAIN) return 0;
            return -1;
        }
    }
#endif
    return -1;
}

/* ---- kqueue filter (un)registration ------------------------------------- */

#if PCC_IO_WAITSET_HAVE_KQUEUE
/* Arm/disarm the EVFILT_READ / EVFILT_WRITE filters for one fd according to its
 * interest mask. On disarm we ignore ENOENT (filter was never armed). Returns 0
 * on success, -1 on a hard error. */
static int pcc_io_kq_update(
    PccIoWaitSet *ws,
    int64_t fd,
    int64_t interest,
    uint32_t generation,
    int add
) {
    struct kevent changes[2];
    int nchg = 0;
    unsigned short flags = add ? (EV_ADD | EV_CLEAR) : EV_DELETE;
    uint64_t token = ((uint64_t)generation << 32) | (uint32_t)fd;
    /* EV_CLEAR gives edge-triggered semantics; we re-derive level behavior in
     * wait() by re-checking the reported readiness against interest, matching
     * the oracle's level-mode re-arm. */
    if (interest & PCC_IO_POLLIN) {
        EV_SET(
            &changes[nchg], (uintptr_t)fd, EVFILT_READ, flags, 0, 0,
            (void *)(uintptr_t)token
        );
        nchg++;
    }
    if (interest & PCC_IO_POLLOUT) {
        EV_SET(
            &changes[nchg], (uintptr_t)fd, EVFILT_WRITE, flags, 0, 0,
            (void *)(uintptr_t)token
        );
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
    int is_new = idx < 0;
    PccIoWaitSlot old_slot;
    memset(&old_slot, 0, sizeof(old_slot));
    if (idx < 0) {
        if (pcc_io_reserve_slot(ws) != 0) return -1;
        idx = ws->len++;
        ws->slots[idx].ready_mask = 0;
        ws->slots[idx].state = 1;
        ws->live_count++;
    } else {
        old_slot = ws->slots[idx];
#if PCC_IO_WAITSET_HAVE_KQUEUE
        if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
            /* Re-registration: drop old filters before re-arming the new mask. */
            (void)pcc_io_kq_update(
                ws, fd, ws->slots[idx].interest,
                ws->slots[idx].generation, 0
            );
        }
#endif
    }
    ws->slots[idx].fd = fd;
    ws->slots[idx].interest = interest;
    ws->slots[idx].deadline = deadline;
    ws->slots[idx].edge = edge ? 1 : 0;
    ws->slots[idx].generation = pcc_io_next_generation(ws);
#if PCC_IO_WAITSET_HAVE_KQUEUE
    if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
        if (pcc_io_kq_update(
            ws, fd, interest, ws->slots[idx].generation, 1
        ) != 0) {
            if (is_new) {
                ws->slots[idx].state = 0;
                ws->live_count--;
                pcc_io_compact(ws);
            } else {
                ws->slots[idx] = old_slot;
                (void)pcc_io_kq_update(
                    ws, old_slot.fd, old_slot.interest,
                    old_slot.generation, 1
                );
            }
            return -1;
        }
    }
#endif
#if PCC_IO_WAITSET_HAVE_EPOLL
    if (ws->backend == PCC_IO_WAITSET_BACKEND_EPOLL) {
        int operation = is_new ? EPOLL_CTL_ADD : EPOLL_CTL_MOD;
        if (pcc_io_epoll_update(ws, &ws->slots[idx], operation) != 0) {
            if (is_new) {
                ws->slots[idx].state = 0;
                ws->live_count--;
                pcc_io_compact(ws);
            } else {
                ws->slots[idx] = old_slot;
            }
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
        (void)pcc_io_kq_update(
            ws, fd, ws->slots[idx].interest,
            ws->slots[idx].generation, 0
        );
    }
#endif
#if PCC_IO_WAITSET_HAVE_EPOLL
    if (ws->backend == PCC_IO_WAITSET_BACKEND_EPOLL) {
        (void)pcc_io_epoll_update(ws, &ws->slots[idx], EPOLL_CTL_DEL);
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

/* ---- absolute deadline helpers ----------------------------------------- */

static int64_t pcc_io_monotonic_ms(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return -1;
    return (int64_t)value.tv_sec * 1000 + (int64_t)value.tv_nsec / 1000000;
}

static int64_t pcc_io_effective_deadline(
    const PccIoWaitSet *ws,
    int64_t wait_deadline,
    int64_t now
) {
    if (ws->live_count <= 0) return now;
    int64_t deadline = wait_deadline;
    for (int64_t i = 0; i < ws->len; i++) {
        const PccIoWaitSlot *slot = &ws->slots[i];
        if (slot->state != 1) continue;
        /* A previous bounded finish may have deferred a cached readiness
         * result.  Do not enter an unbounded kernel wait before draining it. */
        if (
            slot->ready_mask
            & (slot->interest | PCC_IO_ALWAYS_REPORTED)
        ) return now;
        if (slot->deadline < 0) continue;
        if (deadline < 0 || slot->deadline < deadline) {
            deadline = slot->deadline;
        }
    }
    return deadline;
}

static int pcc_io_remaining_ms(int64_t now, int64_t deadline) {
    if (deadline < 0) return -1;
    if (deadline <= now) return 0;
    int64_t remaining = deadline - now;
    return remaining > INT32_MAX ? INT32_MAX : (int)remaining;
}

/* ---- split live wait --------------------------------------------------- */

void pcc_io_waitset_wait_discard(PccIoWaitBatch *batch) {
    if (batch == NULL) return;
    free(batch->events);
    memset(batch, 0, sizeof(*batch));
}

int pcc_io_waitset_wait_prepare(
    PccIoWaitSet *ws,
    int64_t now,
    int64_t wait_deadline,
    PccIoWaitBatch *batch
) {
    if (ws == NULL || batch == NULL) return -1;
    memset(batch, 0, sizeof(*batch));
    if (
        ws->backend != PCC_IO_WAITSET_BACKEND_KQUEUE
        && ws->backend != PCC_IO_WAITSET_BACKEND_EPOLL
    ) return -1;
    int64_t bounded_live = ws->live_count;
    if (bounded_live < 1) bounded_live = 1;
    if (bounded_live > 256) bounded_live = 256;
    int64_t capacity = bounded_live + 1; /* + compiler-owned wake event */
    size_t event_size = 0;
    if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
#if PCC_IO_WAITSET_HAVE_KQUEUE
        capacity = bounded_live * 2 + 1;
        event_size = sizeof(struct kevent);
#else
        return -1;
#endif
    } else {
#if PCC_IO_WAITSET_HAVE_EPOLL
        event_size = sizeof(struct epoll_event);
#else
        return -1;
#endif
    }
    /* Reserve every fallible output allocation before kevent/epoll_wait can
     * consume an edge or disarm an EPOLLONESHOT registration.  The extra
     * event-capacity allowance covers registrations added while block() runs
     * without the ownership lock; finish() bounds any still-larger timeout
     * burst and leaves it live for the next immediate drain. */
    if (ws->live_count > INT64_MAX - capacity) return -1;
    if (pcc_io_reserve_output(ws, ws->live_count + capacity) != 0) return -1;
    batch->events = malloc((size_t)capacity * event_size);
    if (batch->events == NULL) return -1;
    batch->event_capacity = capacity;
    batch->event_count = 0;
    batch->current_now = now;
    batch->effective_deadline = pcc_io_effective_deadline(
        ws, wait_deadline, now
    );
    batch->wait_deadline = wait_deadline;
    batch->backend = ws->backend;
    batch->status = 0;
    return 0;
}

int pcc_io_waitset_wait_block(PccIoWaitSet *ws, PccIoWaitBatch *batch) {
    if (
        ws == NULL || batch == NULL || batch->events == NULL
        || batch->backend != ws->backend
    ) {
        if (batch != NULL) batch->status = -1;
        return -1;
    }
    int count = -1;
    for (;;) {
        int remaining = pcc_io_remaining_ms(
            batch->current_now, batch->effective_deadline
        );
        if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
#if PCC_IO_WAITSET_HAVE_KQUEUE
            struct timespec timeout;
            struct timespec *timeout_ptr = NULL;
            if (remaining >= 0) {
                timeout.tv_sec = remaining / 1000;
                timeout.tv_nsec = (long)(remaining % 1000) * 1000000L;
                timeout_ptr = &timeout;
            }
            count = kevent(
                ws->kq_fd,
                NULL,
                0,
                (struct kevent *)batch->events,
                (int)batch->event_capacity,
                timeout_ptr
            );
#else
            batch->status = -1;
            return -1;
#endif
        } else if (ws->backend == PCC_IO_WAITSET_BACKEND_EPOLL) {
#if PCC_IO_WAITSET_HAVE_EPOLL
            count = epoll_wait(
                ws->kq_fd,
                (struct epoll_event *)batch->events,
                (int)batch->event_capacity,
                remaining
            );
#else
            batch->status = -1;
            return -1;
#endif
        } else {
            batch->status = -1;
            return -1;
        }
        if (count >= 0 || errno != EINTR) break;
        int64_t observed = pcc_io_monotonic_ms();
        if (observed >= 0) batch->current_now = observed;
        if (
            batch->effective_deadline >= 0
            && batch->current_now >= batch->effective_deadline
        ) {
            count = 0;
            break;
        }
    }
    int64_t observed = pcc_io_monotonic_ms();
    if (observed >= 0) batch->current_now = observed;
    batch->event_count = count;
    batch->status = count < 0 ? -1 : 0;
    return batch->status;
}

static void pcc_io_drain_epoll_interrupt(PccIoWaitSet *ws) {
#if PCC_IO_WAITSET_HAVE_EPOLL
    if (ws->backend != PCC_IO_WAITSET_BACKEND_EPOLL || ws->wake_fd < 0) return;
    uint64_t value = 0;
    for (;;) {
        ssize_t count = read(ws->wake_fd, &value, sizeof(value));
        if (count == (ssize_t)sizeof(value)) continue;
        if (count < 0 && errno == EINTR) continue;
        return;
    }
#else
    (void)ws;
#endif
}

int pcc_io_waitset_wait_finish(
    PccIoWaitSet *ws,
    PccIoWaitBatch *batch,
    PccIoWaitResult *out
) {
    if (ws == NULL || batch == NULL || out == NULL) return -1;
    out->ready = NULL;
    out->ready_len = 0;
    out->timed_out = NULL;
    out->timeout_len = 0;
    if (
        batch->status != 0 || batch->event_count < 0
        || batch->backend != ws->backend
    ) {
        pcc_io_waitset_wait_discard(batch);
        return -1;
    }
    if (batch->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
#if PCC_IO_WAITSET_HAVE_KQUEUE
        struct kevent *events = (struct kevent *)batch->events;
        for (int64_t i = 0; i < batch->event_count; i++) {
            uint64_t token = (uint64_t)(uintptr_t)events[i].udata;
            if (token == PCC_IO_WAITSET_WAKE_TOKEN) continue;
            int64_t fd = (int64_t)(uint32_t)token;
            uint32_t generation = (uint32_t)(token >> 32);
            int64_t idx = pcc_io_find_slot_generation(ws, fd, generation);
            if (idx < 0) continue;
            int64_t bits = 0;
            if (events[i].filter == EVFILT_READ) bits |= PCC_IO_POLLIN;
            else if (events[i].filter == EVFILT_WRITE) bits |= PCC_IO_POLLOUT;
            if (events[i].flags & EV_EOF) bits |= PCC_IO_POLLHUP;
            if (events[i].flags & EV_ERROR) bits |= PCC_IO_POLLERR;
            ws->slots[idx].ready_mask |= bits;
        }
#else
        pcc_io_waitset_wait_discard(batch);
        return -1;
#endif
    } else if (batch->backend == PCC_IO_WAITSET_BACKEND_EPOLL) {
#if PCC_IO_WAITSET_HAVE_EPOLL
        struct epoll_event *events = (struct epoll_event *)batch->events;
        for (int64_t i = 0; i < batch->event_count; i++) {
            uint64_t token = events[i].data.u64;
            if (token == PCC_IO_WAITSET_WAKE_TOKEN) continue;
            int64_t fd = (int64_t)(uint32_t)token;
            uint32_t generation = (uint32_t)(token >> 32);
            int64_t idx = pcc_io_find_slot_generation(ws, fd, generation);
            if (idx < 0) continue;
            int64_t bits = 0;
            if (events[i].events & EPOLLIN) bits |= PCC_IO_POLLIN;
            if (events[i].events & EPOLLOUT) bits |= PCC_IO_POLLOUT;
            if (events[i].events & EPOLLERR) bits |= PCC_IO_POLLERR;
            if (events[i].events & (EPOLLHUP | 0x2000U)) {
                bits |= PCC_IO_POLLHUP;
            }
            ws->slots[idx].ready_mask |= bits;
        }
        pcc_io_drain_epoll_interrupt(ws);
#else
        pcc_io_waitset_wait_discard(batch);
        return -1;
#endif
    } else {
        pcc_io_waitset_wait_discard(batch);
        return -1;
    }

    int64_t current_now = batch->current_now;
    pcc_io_waitset_wait_discard(batch);
    /* A mutation may have shortened an aggregate fd deadline while block()
     * ran without the ownership lock. Observe the clock after re-locking so
     * finish does not defer that newly-expired registration to another wait. */
    int64_t finish_now = pcc_io_monotonic_ms();
    if (finish_now >= 0) current_now = finish_now;
    int64_t nready = 0;
    for (int64_t i = 0; i < ws->len; i++) {
        PccIoWaitSlot *slot = &ws->slots[i];
        if (slot->state != 1) continue;
        int64_t hit = slot->ready_mask
            & (slot->interest | PCC_IO_ALWAYS_REPORTED);
        if (hit == 0) continue;
        /* Concurrent add/expiry bursts can make live_count exceed the
         * prepare-time snapshot.  Never overrun the pre-reserved buffer: the
         * cached ready_mask makes a deferred slot an immediate next drain. */
        if (nready >= ws->ready_cap) continue;
        ws->ready_buf[nready].fd = slot->fd;
        ws->ready_buf[nready].events = hit;
        nready++;
#if PCC_IO_WAITSET_HAVE_KQUEUE
        if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
            (void)pcc_io_kq_update(
                ws, slot->fd, slot->interest, slot->generation, 0
            );
        }
#endif
#if PCC_IO_WAITSET_HAVE_EPOLL
        if (ws->backend == PCC_IO_WAITSET_BACKEND_EPOLL) {
            (void)pcc_io_epoll_update(ws, slot, EPOLL_CTL_DEL);
        }
#endif
        slot->state = 0;
        ws->live_count--;
    }

    int64_t ntimeout = 0;
    for (int64_t i = 0; i < ws->len; i++) {
        PccIoWaitSlot *slot = &ws->slots[i];
        if (slot->state != 1) continue;
        if (slot->deadline < 0 || slot->deadline > current_now) continue;
        if (ntimeout >= ws->timeout_cap) continue;
        ws->timeout_buf[ntimeout++] = slot->fd;
#if PCC_IO_WAITSET_HAVE_KQUEUE
        if (ws->backend == PCC_IO_WAITSET_BACKEND_KQUEUE) {
            (void)pcc_io_kq_update(
                ws, slot->fd, slot->interest, slot->generation, 0
            );
        }
#endif
#if PCC_IO_WAITSET_HAVE_EPOLL
        if (ws->backend == PCC_IO_WAITSET_BACKEND_EPOLL) {
            (void)pcc_io_epoll_update(ws, slot, EPOLL_CTL_DEL);
        }
#endif
        slot->state = 0;
        ws->live_count--;
    }
    pcc_io_compact(ws);
    out->ready = ws->ready_buf;
    out->ready_len = nready;
    out->timed_out = ws->timeout_buf;
    out->timeout_len = ntimeout;
    return 0;
}

int pcc_io_waitset_wait_until(
    PccIoWaitSet *ws,
    int64_t now,
    int64_t wait_deadline,
    PccIoWaitResult *out
) {
    if (ws == NULL || out == NULL) return -1;
    out->ready = NULL;
    out->ready_len = 0;
    out->timed_out = NULL;
    out->timeout_len = 0;
    if (ws->backend == PCC_IO_WAITSET_BACKEND_POLL) {
        return pcc_io_wait_poll(ws, now, out);
    }
    PccIoWaitBatch batch;
    if (pcc_io_waitset_wait_prepare(ws, now, wait_deadline, &batch) != 0) {
        return -1;
    }
    (void)pcc_io_waitset_wait_block(ws, &batch);
    return pcc_io_waitset_wait_finish(ws, &batch, out);
}

int pcc_io_waitset_wait(PccIoWaitSet *ws, int64_t now, PccIoWaitResult *out) {
    return pcc_io_waitset_wait_until(ws, now, now, out);
}

/* ---- platform capability + skip reason ---------------------------------- */

int pcc_io_waitset_kqueue_available(void) {
    return PCC_IO_WAITSET_HAVE_KQUEUE ? 1 : 0;
}

int pcc_io_waitset_real_kqueue_skip(PccIoWaitSetSkip *out) {
#if PCC_IO_WAITSET_HAVE_KQUEUE
    (void)out;
    return 0;
#else
    if (out != NULL) {
        out->path = "io_waitset.real_kqueue";
        out->reason =
            "real kqueue/kevent requires Darwin/BSD; Linux uses epoll and "
            "other targets use the explicitly labeled poll fallback.";
    }
    return 1;
#endif
}

int pcc_io_waitset_epoll_available(void) {
    return PCC_IO_WAITSET_HAVE_EPOLL ? 1 : 0;
}

int pcc_io_waitset_real_epoll_skip(PccIoWaitSetSkip *out) {
#if PCC_IO_WAITSET_HAVE_EPOLL
    (void)out;
    return 0;
#else
    if (out != NULL) {
        out->path = "io_waitset.real_epoll";
        out->reason = "real epoll is a Linux-only readiness backend";
    }
    return 1;
#endif
}

const char *pcc_io_waitset_backend_label(int64_t backend) {
    if (backend == PCC_IO_WAITSET_BACKEND_POLL) return "poll";
    if (backend == PCC_IO_WAITSET_BACKEND_KQUEUE) return "kqueue";
    if (backend == PCC_IO_WAITSET_BACKEND_EPOLL) return "epoll";
    return "unknown";
}

int64_t pcc_io_waitset_default_backend(void) {
#if PCC_IO_WAITSET_HAVE_KQUEUE
    return PCC_IO_WAITSET_BACKEND_KQUEUE;
#elif PCC_IO_WAITSET_HAVE_EPOLL
    return PCC_IO_WAITSET_BACKEND_EPOLL;
#else
    return PCC_IO_WAITSET_BACKEND_POLL;
#endif
}
