/* pcc/py_runtime/src/pcc_threads.c
 *
 * Shared threading substrate for GC backends.
 *
 * Default builds are deliberately single-threaded. Build with
 * PCC_WITH_THREADS=1 to enable the pthread-backed wrappers. Atomic
 * refcount can also be selected independently with
 * PCC_REFCOUNT_STRATEGY=PCC_REFCOUNT_KIND_ATOMIC.
 */

#include "py_internal.h"
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <poll.h>
#include <sys/time.h>

/* ``pcc_gc_thread_unregister_buffers`` is defined for real in
 * ``py_gc_backend.c``. The previous fallback here was
 * ``__attribute__((weak)) void pcc_gc_thread_unregister_buffers(void)
 * {}``, which let system clang/gcc dedupe against the strong
 * definition. pcc's own C frontend doesn't lower the ``weak``
 * attribute (yet), so under pcc's libpy_runtime_pcc.a build the
 * placeholder + real definition collided as duplicate strong
 * symbols. Both ``pcc_threads.c`` and ``py_gc_backend.c`` are always
 * compiled into every archive variant, so an ``extern`` declaration
 * is sufficient: the call at the bottom of this file resolves to
 * the real definition via the linker. */
extern void pcc_gc_thread_unregister_buffers(void);

#ifndef PCC_WITH_THREADS
#define PCC_WITH_THREADS 0
#endif

#ifndef PCC_REFCOUNT_STRATEGY
#if PCC_WITH_THREADS
#define PCC_REFCOUNT_STRATEGY PCC_REFCOUNT_KIND_ATOMIC
#else
#define PCC_REFCOUNT_STRATEGY PCC_REFCOUNT_KIND_NONATOMIC
#endif
#endif

#if PCC_REFCOUNT_STRATEGY < PCC_REFCOUNT_KIND_NONATOMIC \
    || PCC_REFCOUNT_STRATEGY > PCC_REFCOUNT_KIND_DEFERRED
#error "PCC_REFCOUNT_STRATEGY must be NONATOMIC, ATOMIC, BIASED, or DEFERRED."
#endif

int64_t pcc_threads_enabled(void) {
    return PCC_WITH_THREADS ? 1 : 0;
}

int64_t pcc_refcount_strategy(void) {
    return (int64_t)PCC_REFCOUNT_STRATEGY;
}

/* Generated loop/function safepoints poll this global directly and only call
 * pcc_thread_safepoint() on the slow path.  Keep it exported even when
 * PCC_WITH_THREADS=0 so the same generated IR links against threaded and
 * non-threaded runtime archives. */
int32_t pcc_thread_stop_requested = 0;

#if PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_BIASED \
    || PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_DEFERRED
/* Refcount strategy metadata.
 *
 * BIASED and DEFERRED are implemented without changing the public object
 * header in this slice.  The metadata is keyed by the address of the
 * refcount slot; py_decref calls pcc_refcount_forget() before storage is
 * released so a later malloc reuse cannot inherit stale owner state.
 *
 * BIASED keeps the PEP-703 shape (owner thread + local/shared counters).
 * A small spin lock protects the side table; this favours correctness and
 * ThreadSanitizer cleanliness over the final no-lock fast path.  A future
 * ABI migration can move owner/local/shared into PyObjectHeader and remove
 * this lock from same-owner INCREF/DECREF.
 *
 * DEFERRED keeps an explicit pending counter.  The synchronous dealloc
 * contract still requires exact zero detection, so decref flushes before it
 * returns; increments may batch until the next decrement/forget/flush. */
typedef struct PccRefcountMeta {
    int64_t *slot;
    int64_t owner_tid;
    int64_t local;
    int64_t shared;
    int64_t pending;
    struct PccRefcountMeta *next;
} PccRefcountMeta;

static PccRefcountMeta *pcc_refmeta_head = NULL;
static unsigned char pcc_refmeta_lock_word = 0;

static void pcc_refmeta_lock(void) {
    while (__atomic_test_and_set(&pcc_refmeta_lock_word, __ATOMIC_ACQUIRE)) {
        pcc_thread_safepoint();
    }
}

static void pcc_refmeta_unlock(void) {
    __atomic_clear(&pcc_refmeta_lock_word, __ATOMIC_RELEASE);
}

static PccRefcountMeta *pcc_refmeta_find_locked(int64_t *slot, int create) {
    for (PccRefcountMeta *m = pcc_refmeta_head; m != NULL; m = m->next) {
        if (m->slot == slot) return m;
    }
    if (!create) return NULL;
    PccRefcountMeta *m = (PccRefcountMeta *)calloc(1, sizeof(PccRefcountMeta));
    if (m == NULL) return NULL;
    m->slot = slot;
    m->owner_tid = pcc_current_thread_id();
    m->local = __atomic_load_n(slot, __ATOMIC_ACQUIRE);
    m->shared = 0;
    m->pending = 0;
    m->next = pcc_refmeta_head;
    pcc_refmeta_head = m;
    return m;
}

static int64_t pcc_refmeta_sync_locked(PccRefcountMeta *m) {
    if (m == NULL || m->slot == NULL) return 0;
    int64_t total = m->local + m->shared + m->pending;
    if (total < 0) total = 0;
    m->local = total;
    m->shared = 0;
    m->pending = 0;
    __atomic_store_n(m->slot, total, __ATOMIC_RELEASE);
    return total;
}

static int64_t pcc_refcount_biased_delta(int64_t *slot, int64_t delta) {
    if (slot == NULL) return 0;
    pcc_refmeta_lock();
    PccRefcountMeta *m = pcc_refmeta_find_locked(slot, 1);
    if (m == NULL) {
        pcc_refmeta_unlock();
        return __atomic_add_fetch(slot, delta, __ATOMIC_ACQ_REL);
    }
    int64_t self = pcc_current_thread_id();
    if (m->owner_tid == 0) m->owner_tid = self;
    if (m->owner_tid == self) {
        m->local += delta;
    } else {
        m->shared += delta;
    }
    int64_t total = pcc_refmeta_sync_locked(m);
    pcc_refmeta_unlock();
    return total;
}

static int64_t pcc_refcount_deferred_delta(int64_t *slot, int64_t delta) {
    if (slot == NULL) return 0;
    pcc_refmeta_lock();
    PccRefcountMeta *m = pcc_refmeta_find_locked(slot, 1);
    if (m == NULL) {
        pcc_refmeta_unlock();
        return __atomic_add_fetch(slot, delta, __ATOMIC_ACQ_REL);
    }
    m->pending += delta;
    /* Decrements must report an exact count so py_decref can decide
     * whether to deallocate immediately.  Increments can batch, but a
     * flush threshold of 32 keeps observable refcount close and bounded. */
    if (delta < 0 || m->pending >= 32 || m->pending <= -32) {
        (void)pcc_refmeta_sync_locked(m);
    }
    int64_t total = m->local + m->shared + m->pending;
    if (delta < 0) total = pcc_refmeta_sync_locked(m);
    pcc_refmeta_unlock();
    return total < 0 ? 0 : total;
}
#endif

int64_t pcc_refcount_incref(int64_t *slot) {
    if (slot == NULL) return 0;
#if PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_ATOMIC
    return __atomic_add_fetch(slot, 1, __ATOMIC_RELAXED);
#elif PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_BIASED
    return pcc_refcount_biased_delta(slot, 1);
#elif PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_DEFERRED
    return pcc_refcount_deferred_delta(slot, 1);
#else
    *slot += 1;
    return *slot;
#endif
}

int64_t pcc_refcount_decref(int64_t *slot) {
    if (slot == NULL) return 0;
#if PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_ATOMIC
    return __atomic_sub_fetch(slot, 1, __ATOMIC_ACQ_REL);
#elif PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_BIASED
    return pcc_refcount_biased_delta(slot, -1);
#elif PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_DEFERRED
    return pcc_refcount_deferred_delta(slot, -1);
#else
    *slot -= 1;
    return *slot;
#endif
}

int64_t pcc_refcount_load(int64_t *slot) {
    if (slot == NULL) return 0;
#if PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_ATOMIC
    return __atomic_load_n(slot, __ATOMIC_ACQUIRE);
#elif PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_BIASED
    return pcc_refcount_biased_delta(slot, 0);
#elif PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_DEFERRED
    return pcc_refcount_deferred_delta(slot, 0);
#else
    return *slot;
#endif
}

void pcc_refcount_forget(int64_t *slot) {
    if (slot == NULL) return;
#if PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_BIASED \
    || PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_DEFERRED
    pcc_refmeta_lock();
    PccRefcountMeta **cur = &pcc_refmeta_head;
    while (*cur != NULL) {
        if ((*cur)->slot == slot) {
            PccRefcountMeta *dead = *cur;
            *cur = dead->next;
            free(dead);
            break;
        }
        cur = &(*cur)->next;
    }
    pcc_refmeta_unlock();
#else
    (void)slot;
#endif
}

#if PCC_WITH_THREADS

#include <errno.h>
#include <pthread.h>
#include <sched.h>

struct PccThreadHandle {
    pthread_t thread;
    pthread_mutex_t state_lock;
    int32_t done;
    int32_t detached;
    void *result;
};

struct PccMutex {
    pthread_mutex_t mutex;
};

struct PccCond {
    pthread_cond_t cond;
};

typedef struct {
    PccThreadMain entry;
    void *arg;
    PccThreadHandle *handle;
} PccThreadStart;

static pthread_mutex_t pcc_world_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t pcc_world_cond = PTHREAD_COND_INITIALIZER;
static int64_t pcc_next_thread_id = 1;
static int64_t pcc_live_thread_count = 0;
static int64_t pcc_parked_thread_count = 0;
static int64_t pcc_stop_owner_thread_id = 0;
static int64_t pcc_stop_epoch = 0;
static int64_t pcc_stop_depth = 0;
static _Thread_local int64_t pcc_tls_thread_id = 0;
static _Thread_local int32_t pcc_tls_thread_parked = 0;
static _Thread_local int64_t pcc_tls_parked_epoch = 0;

static void pcc_thread_unregister_current(void) {
    pcc_gc_thread_unregister_buffers();
    pthread_mutex_lock(&pcc_world_lock);
    if (pcc_tls_thread_id != 0) {
        if (pcc_tls_thread_parked) {
            pcc_tls_thread_parked = 0;
            if (pcc_tls_parked_epoch == pcc_stop_epoch
                && pcc_parked_thread_count > 0) {
                pcc_parked_thread_count--;
            }
            pcc_tls_parked_epoch = 0;
        }
        pcc_tls_thread_id = 0;
        if (pcc_live_thread_count > 0) pcc_live_thread_count--;
        pthread_cond_broadcast(&pcc_world_cond);
    }
    pthread_mutex_unlock(&pcc_world_lock);
}

int64_t pcc_current_thread_id(void) {
    if (pcc_tls_thread_id != 0) return pcc_tls_thread_id;
    pthread_mutex_lock(&pcc_world_lock);
    if (pcc_tls_thread_id == 0) {
        pcc_tls_thread_id = pcc_next_thread_id++;
        pcc_live_thread_count++;
        pthread_cond_broadcast(&pcc_world_cond);
    }
    int64_t tid = pcc_tls_thread_id;
    pthread_mutex_unlock(&pcc_world_lock);
    return tid;
}

void pcc_thread_safepoint(void) {
    int64_t self = pcc_current_thread_id();
    pthread_mutex_lock(&pcc_world_lock);
    while (pcc_thread_stop_requested && pcc_stop_owner_thread_id != self) {
        if (!pcc_tls_thread_parked
            || pcc_tls_parked_epoch != pcc_stop_epoch) {
            pcc_tls_thread_parked = 1;
            pcc_tls_parked_epoch = pcc_stop_epoch;
            pcc_parked_thread_count++;
            pthread_cond_broadcast(&pcc_world_cond);
        }
        pthread_cond_wait(&pcc_world_cond, &pcc_world_lock);
    }
    if (!pcc_thread_stop_requested && pcc_tls_thread_parked) {
        pcc_tls_thread_parked = 0;
        pcc_tls_parked_epoch = 0;
    }
    pthread_mutex_unlock(&pcc_world_lock);
}

int64_t pcc_stop_the_world(void) {
    int64_t self = pcc_current_thread_id();
    pthread_mutex_lock(&pcc_world_lock);
    if (pcc_thread_stop_requested) {
        if (pcc_stop_owner_thread_id == self) {
            pcc_stop_depth++;
            pthread_mutex_unlock(&pcc_world_lock);
            return 0;
        }
        pthread_mutex_unlock(&pcc_world_lock);
        return -1;
    }
    pcc_thread_stop_requested = 1;
    pcc_stop_owner_thread_id = self;
    pcc_stop_depth = 1;
    pcc_stop_epoch++;
    if (pcc_stop_epoch <= 0) pcc_stop_epoch = 1;
    pcc_parked_thread_count = 0;
    pthread_cond_broadcast(&pcc_world_cond);
    while (pcc_live_thread_count > 1
           && pcc_parked_thread_count < pcc_live_thread_count - 1) {
        pthread_cond_wait(&pcc_world_cond, &pcc_world_lock);
    }
    pthread_mutex_unlock(&pcc_world_lock);
    return 0;
}

int64_t pcc_resume_world(void) {
    pthread_mutex_lock(&pcc_world_lock);
    if (!pcc_thread_stop_requested) {
        pthread_mutex_unlock(&pcc_world_lock);
        return -1;
    }
    if (
        pcc_stop_owner_thread_id == pcc_current_thread_id()
        && pcc_stop_depth > 1
    ) {
        pcc_stop_depth--;
        pthread_mutex_unlock(&pcc_world_lock);
        return 0;
    }
    pcc_thread_stop_requested = 0;
    pcc_stop_owner_thread_id = 0;
    pcc_stop_depth = 0;
    pcc_parked_thread_count = 0;
    pthread_cond_broadcast(&pcc_world_cond);
    pthread_mutex_unlock(&pcc_world_lock);
    return 0;
}

static void *pcc_thread_trampoline(void *opaque) {
    PccThreadStart *start = (PccThreadStart *)opaque;
    PccThreadMain entry = start->entry;
    void *arg = start->arg;
    PccThreadHandle *handle = start->handle;
    free(start);

    (void)pcc_current_thread_id();
    pcc_thread_safepoint();
    void *result = entry(arg);
    pcc_thread_unregister_current();
    int should_free = 0;
    pthread_mutex_lock(&handle->state_lock);
    handle->result = result;
    handle->done = 1;
    should_free = handle->detached;
    pthread_mutex_unlock(&handle->state_lock);
    if (should_free) {
        pthread_mutex_destroy(&handle->state_lock);
        free(handle);
    }
    return result;
}

int64_t pcc_thread_start(
    PccThreadHandle **out,
    PccThreadMain entry,
    void *arg
) {
    if (out == NULL || entry == NULL) return -1;
    PccThreadHandle *handle = (PccThreadHandle *)calloc(1, sizeof(PccThreadHandle));
    if (handle == NULL) return -1;
    if (pthread_mutex_init(&handle->state_lock, NULL) != 0) {
        free(handle);
        return -1;
    }
    PccThreadStart *start = (PccThreadStart *)calloc(1, sizeof(PccThreadStart));
    if (start == NULL) {
        pthread_mutex_destroy(&handle->state_lock);
        free(handle);
        return -1;
    }
    start->entry = entry;
    start->arg = arg;
    start->handle = handle;
    int rc = pthread_create(&handle->thread, NULL, pcc_thread_trampoline, start);
    if (rc != 0) {
        free(start);
        pthread_mutex_destroy(&handle->state_lock);
        free(handle);
        return -1;
    }
    *out = handle;
    return 0;
}

int64_t pcc_thread_join(PccThreadHandle *thread, void **result) {
    if (thread == NULL) return -1;
    for (;;) {
        pthread_mutex_lock(&thread->state_lock);
        int32_t done = thread->done;
        void *local_result = thread->result;
        pthread_mutex_unlock(&thread->state_lock);
        if (done) {
            void *joined_result = NULL;
            int rc = pthread_join(thread->thread, &joined_result);
            if (result != NULL) {
                *result = rc == 0 ? joined_result : local_result;
            }
            pthread_mutex_destroy(&thread->state_lock);
            free(thread);
            return rc == 0 ? 0 : -1;
        }
        pcc_thread_safepoint();
        sched_yield();
    }
}

void pcc_thread_detach(PccThreadHandle *thread) {
    if (thread == NULL) return;
    (void)pthread_detach(thread->thread);
    int should_free = 0;
    pthread_mutex_lock(&thread->state_lock);
    thread->detached = 1;
    should_free = thread->done;
    pthread_mutex_unlock(&thread->state_lock);
    if (should_free) {
        pthread_mutex_destroy(&thread->state_lock);
        free(thread);
    }
}

PccMutex *pcc_mutex_new(void) {
    PccMutex *m = (PccMutex *)calloc(1, sizeof(PccMutex));
    if (m == NULL) return NULL;
    if (pthread_mutex_init(&m->mutex, NULL) != 0) {
        free(m);
        return NULL;
    }
    return m;
}

void pcc_mutex_free(PccMutex *mutex) {
    if (mutex == NULL) return;
    (void)pthread_mutex_destroy(&mutex->mutex);
    free(mutex);
}

int64_t pcc_mutex_lock(PccMutex *mutex) {
    if (mutex == NULL) return -1;
    for (;;) {
        int rc = pthread_mutex_trylock(&mutex->mutex);
        if (rc == 0) return 0;
        if (rc != EBUSY) return -1;
        pcc_thread_safepoint();
        sched_yield();
    }
}

int64_t pcc_mutex_unlock(PccMutex *mutex) {
    if (mutex == NULL) return -1;
    return pthread_mutex_unlock(&mutex->mutex) == 0 ? 0 : -1;
}

PccCond *pcc_cond_new(void) {
    PccCond *c = (PccCond *)calloc(1, sizeof(PccCond));
    if (c == NULL) return NULL;
    if (pthread_cond_init(&c->cond, NULL) != 0) {
        free(c);
        return NULL;
    }
    return c;
}

void pcc_cond_free(PccCond *cond) {
    if (cond == NULL) return;
    (void)pthread_cond_destroy(&cond->cond);
    free(cond);
}

int64_t pcc_cond_wait(PccCond *cond, PccMutex *mutex) {
    if (cond == NULL || mutex == NULL) return -1;
    return pthread_cond_wait(&cond->cond, &mutex->mutex) == 0 ? 0 : -1;
}

/* Bounded condition wait. Returns 0 if signaled, 1 if it timed out, -1 on
 * error. Callers that hold a runtime lock while blocking on a Python-level
 * primitive (e.g. threading.Lock.acquire) must use this plus
 * pcc_thread_safepoint() instead of pcc_cond_wait, so a thread waiting for the
 * lock can still reach a safepoint and park during stop-the-world collection.
 * An unbounded pcc_cond_wait deadlocks: the STW initiator waits for this
 * thread to park, this thread waits for the lock, and the lock owner is itself
 * parked at a safepoint and cannot release. See
 * docs/investigations/pcc1-threaded-explicit-gc-backend0-double-free-highscale.md */
int64_t pcc_cond_timedwait_ms(PccCond *cond, PccMutex *mutex, int64_t timeout_ms) {
    if (cond == NULL || mutex == NULL) return -1;
    if (timeout_ms < 0) timeout_ms = 0;
    struct timeval now;
    gettimeofday(&now, NULL);
    long add_ns = (long)(timeout_ms % 1000) * 1000000L + (long)now.tv_usec * 1000L;
    struct timespec ts;
    ts.tv_sec = now.tv_sec + (time_t)(timeout_ms / 1000) + (time_t)(add_ns / 1000000000L);
    ts.tv_nsec = add_ns % 1000000000L;
    int rc = pthread_cond_timedwait(&cond->cond, &mutex->mutex, &ts);
    if (rc == 0) return 0;
    if (rc == ETIMEDOUT) return 1;
    return -1;
}

int64_t pcc_cond_signal(PccCond *cond) {
    if (cond == NULL) return -1;
    return pthread_cond_signal(&cond->cond) == 0 ? 0 : -1;
}

int64_t pcc_cond_broadcast(PccCond *cond) {
    if (cond == NULL) return -1;
    return pthread_cond_broadcast(&cond->cond) == 0 ? 0 : -1;
}

#else  /* !PCC_WITH_THREADS */

struct PccThreadHandle { int unused; };
struct PccMutex { int unused; };
struct PccCond { int unused; };

int64_t pcc_current_thread_id(void) {
    return 1;
}

void pcc_thread_safepoint(void) {
}

int64_t pcc_stop_the_world(void) {
    return 0;
}

int64_t pcc_resume_world(void) {
    return 0;
}

int64_t pcc_thread_start(
    PccThreadHandle **out,
    PccThreadMain entry,
    void *arg
) {
    (void)out;
    (void)entry;
    (void)arg;
    return -1;
}

int64_t pcc_thread_join(PccThreadHandle *thread, void **result) {
    (void)thread;
    if (result != NULL) *result = NULL;
    return -1;
}

void pcc_thread_detach(PccThreadHandle *thread) {
    (void)thread;
}

/* Non-NULL sentinel: callers gate on NULL-check, so a stub mutex still needs
 * to look "allocated" without dereferencing. The lock/unlock no-ops never
 * read through the pointer. */
static char _pcc_mutex_stub;
static char _pcc_cond_stub;
PccMutex *pcc_mutex_new(void) { return (PccMutex *)&_pcc_mutex_stub; }
void pcc_mutex_free(PccMutex *mutex) { (void)mutex; }
int64_t pcc_mutex_lock(PccMutex *mutex) { (void)mutex; return 0; }
int64_t pcc_mutex_unlock(PccMutex *mutex) { (void)mutex; return 0; }

PccCond *pcc_cond_new(void) { return (PccCond *)&_pcc_cond_stub; }
void pcc_cond_free(PccCond *cond) { (void)cond; }
int64_t pcc_cond_wait(PccCond *cond, PccMutex *mutex) {
    (void)cond;
    (void)mutex;
    return 0;
}
int64_t pcc_cond_timedwait_ms(PccCond *cond, PccMutex *mutex, int64_t timeout_ms) {
    (void)cond;
    (void)mutex;
    (void)timeout_ms;
    return 0;
}
int64_t pcc_cond_signal(PccCond *cond) { (void)cond; return 0; }
int64_t pcc_cond_broadcast(PccCond *cond) { (void)cond; return 0; }

#endif  /* PCC_WITH_THREADS */

enum {
    PCC_VTHREAD_NEW = 0,
    PCC_VTHREAD_READY = 1,
    PCC_VTHREAD_RUNNING = 2,
    PCC_VTHREAD_PARKED = 3,
    PCC_VTHREAD_DONE = 4
};

typedef struct PccVirtualThreadQueueEntry {
    PyObject *thread;
    struct PccVirtualThreadQueueEntry *next;
} PccVirtualThreadQueueEntry;

typedef struct PccVirtualThreadCarrierQueue {
    PccVirtualThreadQueueEntry *head;
    PccVirtualThreadQueueEntry *tail;
} PccVirtualThreadCarrierQueue;

typedef struct PccVirtualThreadTimerEntry {
    PyObject *thread;
    int64_t deadline_ms;
    struct PccVirtualThreadTimerEntry *next;
} PccVirtualThreadTimerEntry;

typedef struct PccVirtualThreadPollEntry {
    PyObject *thread;
    int64_t fd;
    int64_t events;
    int64_t deadline_ms;
    struct PccVirtualThreadPollEntry *next;
} PccVirtualThreadPollEntry;

static PccMutex *pcc_vthread_lock = NULL;
static PccVirtualThreadQueueEntry *pcc_vthread_ready_queue = NULL;
static PccVirtualThreadQueueEntry *pcc_vthread_ready_tail = NULL;
static PccVirtualThreadCarrierQueue *pcc_vthread_carrier_queues = NULL;
static int64_t pcc_vthread_carrier_queue_count = 0;
static int64_t pcc_vthread_next_carrier_enqueue = 0;
static int64_t pcc_vthread_carrier_steal_count = 0;
static PccVirtualThreadTimerEntry *pcc_vthread_timer_queue = NULL;
static PccVirtualThreadPollEntry *pcc_vthread_poll_queue = NULL;
static int64_t pcc_vthread_ready_count_value = 0;
static int64_t pcc_vthread_timer_count_value = 0;
static int64_t pcc_vthread_io_wait_count_value = 0;
static int64_t pcc_vthread_carrier_count = 1;
static int64_t pcc_vthread_pin_depth_total = 0;
static int64_t pcc_vthread_pin_events = 0;
static _Thread_local PyObject *pcc_current_virtual_thread = NULL;
static _Thread_local int64_t pcc_current_virtual_thread_carrier = -1;
static PccThreadHandle **pcc_vthread_persistent_carriers = NULL;
static int64_t *pcc_vthread_persistent_carrier_indices = NULL;
static int64_t pcc_vthread_persistent_carrier_count = 0;
static int64_t pcc_vthread_persistent_pool_running = 0;
static int64_t pcc_vthread_persistent_pool_stop = 0;
static int64_t pcc_vthread_persistent_pool_failures = 0;

static int pcc_vthread_scheduler_init(void) {
    if (pcc_vthread_lock != NULL) return 0;
    pcc_vthread_lock = pcc_mutex_new();
    return pcc_vthread_lock != NULL ? 0 : -1;
}

static int64_t pcc_vthread_now_ms(void) {
    struct timeval tv;
    if (gettimeofday(&tv, NULL) != 0) return 0;
    return ((int64_t)tv.tv_sec * 1000) + ((int64_t)tv.tv_usec / 1000);
}

static PyVirtualThreadObject *checked_vthread(PyObject *vthread) {
    if (vthread == NULL || PY_IS_TAGGED_INT(vthread)) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "object is not a virtual thread"));
        return NULL;
    }
    vthread = pcc_gc_note_relocation_read(vthread);
    if (py_type_of(vthread) != PY_TYPE_VIRTUAL_THREAD) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "object is not a virtual thread"));
        return NULL;
    }
    return (PyVirtualThreadObject *)vthread;
}

static void pcc_vthread_queue_push_entry_locked(
    PccVirtualThreadQueueEntry **head,
    PccVirtualThreadQueueEntry **tail,
    PccVirtualThreadQueueEntry *entry
) {
    entry->next = NULL;
    if (*tail == NULL) {
        *head = entry;
        *tail = entry;
    } else {
        (*tail)->next = entry;
        *tail = entry;
    }
}

static void pcc_vthread_push_ready_entry_locked(
    PccVirtualThreadQueueEntry *entry
) {
    if (
        pcc_vthread_carrier_queues != NULL
        && pcc_vthread_carrier_queue_count > 0
    ) {
        int64_t idx = pcc_vthread_next_carrier_enqueue
            % pcc_vthread_carrier_queue_count;
        pcc_vthread_next_carrier_enqueue++;
        pcc_vthread_queue_push_entry_locked(
            &pcc_vthread_carrier_queues[idx].head,
            &pcc_vthread_carrier_queues[idx].tail,
            entry
        );
        return;
    }
    pcc_vthread_queue_push_entry_locked(
        &pcc_vthread_ready_queue,
        &pcc_vthread_ready_tail,
        entry
    );
}

static int pcc_vthread_enqueue_locked(PyObject *vthread) {
    PyVirtualThreadObject *vt = (PyVirtualThreadObject *)vthread;
    if (vt->queued != 0 || vt->state != PCC_VTHREAD_READY) return 0;
    PccVirtualThreadQueueEntry *entry = (
        PccVirtualThreadQueueEntry *
    )calloc(1, sizeof(PccVirtualThreadQueueEntry));
    if (entry == NULL) return -1;
    pcc_gc_scheduler_root_register(&entry->thread);
    pcc_gc_store_root(&entry->thread, vthread);
    pcc_vthread_push_ready_entry_locked(entry);
    vt->queued = 1;
    pcc_vthread_ready_count_value++;
    return 0;
}

static int pcc_vthread_make_ready_locked(PyVirtualThreadObject *vt) {
    if (vt == NULL || vt->state == PCC_VTHREAD_DONE) return 0;
    vt->state = PCC_VTHREAD_READY;
    return pcc_vthread_enqueue_locked((PyObject *)vt);
}

static PyObject *pcc_vthread_dequeue_from_queue_locked(
    PccVirtualThreadQueueEntry **head,
    PccVirtualThreadQueueEntry **tail
) {
    while (*head != NULL) {
        PccVirtualThreadQueueEntry *entry = *head;
        *head = entry->next;
        if (*head == NULL) *tail = NULL;
        if (pcc_vthread_ready_count_value > 0) pcc_vthread_ready_count_value--;

        PyObject *thread = pcc_gc_load_ptr(NULL, &entry->thread);
        PyVirtualThreadObject *vt = NULL;
        if (
            thread != NULL
            && !PY_IS_TAGGED_INT(thread)
            && py_type_of(thread) == PY_TYPE_VIRTUAL_THREAD
        ) {
            vt = (PyVirtualThreadObject *)thread;
            vt->queued = 0;
        }
        py_incref(thread);
        pcc_gc_scheduler_root_unregister(&entry->thread);
        pcc_gc_store_root(&entry->thread, NULL);
        free(entry);
        if (vt != NULL && vt->state == PCC_VTHREAD_READY) {
            vt->state = PCC_VTHREAD_RUNNING;
            return thread;
        }
        py_decref(thread);
    }
    return NULL;
}

static PyObject *pcc_vthread_dequeue_locked(void) {
    int64_t own = pcc_current_virtual_thread_carrier;
    int64_t n_queues = pcc_vthread_carrier_queue_count;
    if (
        pcc_vthread_carrier_queues != NULL
        && own >= 0
        && own < n_queues
    ) {
        PyObject *thread = pcc_vthread_dequeue_from_queue_locked(
            &pcc_vthread_carrier_queues[own].head,
            &pcc_vthread_carrier_queues[own].tail
        );
        if (thread != NULL) return thread;
        for (int64_t offset = 1; offset < n_queues; offset++) {
            int64_t idx = (own + offset) % n_queues;
            thread = pcc_vthread_dequeue_from_queue_locked(
                &pcc_vthread_carrier_queues[idx].head,
                &pcc_vthread_carrier_queues[idx].tail
            );
            if (thread != NULL) {
                __atomic_add_fetch(
                    &pcc_vthread_carrier_steal_count,
                    1,
                    __ATOMIC_ACQ_REL
                );
                return thread;
            }
        }
    } else if (pcc_vthread_carrier_queues != NULL && n_queues > 0) {
        for (int64_t idx = 0; idx < n_queues; idx++) {
            PyObject *thread = pcc_vthread_dequeue_from_queue_locked(
                &pcc_vthread_carrier_queues[idx].head,
                &pcc_vthread_carrier_queues[idx].tail
            );
            if (thread != NULL) return thread;
        }
    }
    return pcc_vthread_dequeue_from_queue_locked(
        &pcc_vthread_ready_queue,
        &pcc_vthread_ready_tail
    );
}

static int pcc_vthread_carrier_queues_open_locked(int64_t carrier_count) {
    if (carrier_count <= 0) return 0;
    if (pcc_vthread_carrier_queues != NULL) return 0;
    PccVirtualThreadCarrierQueue *queues = (
        PccVirtualThreadCarrierQueue *
    )calloc((size_t)carrier_count, sizeof(PccVirtualThreadCarrierQueue));
    if (queues == NULL) return -1;
    pcc_vthread_carrier_queues = queues;
    pcc_vthread_carrier_queue_count = carrier_count;
    pcc_vthread_next_carrier_enqueue = 0;
    return 0;
}

static void pcc_vthread_carrier_queues_close_locked(void) {
    PccVirtualThreadCarrierQueue *queues = pcc_vthread_carrier_queues;
    int64_t count = pcc_vthread_carrier_queue_count;
    if (queues == NULL || count <= 0) {
        pcc_vthread_carrier_queues = NULL;
        pcc_vthread_carrier_queue_count = 0;
        return;
    }
    for (int64_t i = 0; i < count; i++) {
        if (queues[i].head == NULL) continue;
        if (pcc_vthread_ready_tail == NULL) {
            pcc_vthread_ready_queue = queues[i].head;
            pcc_vthread_ready_tail = queues[i].tail;
        } else {
            pcc_vthread_ready_tail->next = queues[i].head;
            pcc_vthread_ready_tail = queues[i].tail;
        }
        queues[i].head = NULL;
        queues[i].tail = NULL;
    }
    pcc_vthread_carrier_queues = NULL;
    pcc_vthread_carrier_queue_count = 0;
    free(queues);
}

static void pcc_vthread_timer_entry_free(PccVirtualThreadTimerEntry *entry) {
    if (entry == NULL) return;
    pcc_gc_scheduler_root_unregister(&entry->thread);
    pcc_gc_store_root(&entry->thread, NULL);
    free(entry);
}

static void pcc_vthread_poll_entry_free(PccVirtualThreadPollEntry *entry) {
    if (entry == NULL) return;
    pcc_gc_scheduler_root_unregister(&entry->thread);
    pcc_gc_store_root(&entry->thread, NULL);
    free(entry);
}

static int pcc_vthread_timer_add_locked(
    PyObject *vthread,
    int64_t deadline_ms
) {
    PyVirtualThreadObject *vt = (PyVirtualThreadObject *)vthread;
    PccVirtualThreadTimerEntry *entry = (
        PccVirtualThreadTimerEntry *
    )calloc(1, sizeof(PccVirtualThreadTimerEntry));
    if (entry == NULL) return -1;
    pcc_gc_scheduler_root_register(&entry->thread);
    pcc_gc_store_root(&entry->thread, vthread);
    entry->deadline_ms = deadline_ms;
    PccVirtualThreadTimerEntry **cur = &pcc_vthread_timer_queue;
    while (*cur != NULL && (*cur)->deadline_ms <= deadline_ms) {
        cur = &(*cur)->next;
    }
    entry->next = *cur;
    *cur = entry;
    vt->queued = 1;
    pcc_vthread_timer_count_value++;
    return 0;
}

static int pcc_vthread_poll_add_locked(
    PyObject *vthread,
    int64_t fd,
    int64_t events,
    int64_t deadline_ms
) {
    PyVirtualThreadObject *vt = (PyVirtualThreadObject *)vthread;
    PccVirtualThreadPollEntry *entry = (
        PccVirtualThreadPollEntry *
    )calloc(1, sizeof(PccVirtualThreadPollEntry));
    if (entry == NULL) return -1;
    pcc_gc_scheduler_root_register(&entry->thread);
    pcc_gc_store_root(&entry->thread, vthread);
    entry->fd = fd;
    entry->events = events;
    entry->deadline_ms = deadline_ms;
    entry->next = pcc_vthread_poll_queue;
    pcc_vthread_poll_queue = entry;
    vt->queued = 1;
    pcc_vthread_io_wait_count_value++;
    return 0;
}

static int pcc_vthread_fd_ready(
    int64_t fd,
    int64_t events,
    int64_t timeout_ms
) {
    if (fd < 0 || fd > INT_MAX) return -1;
    if (events == 0) events = POLLIN;
    int timeout = 0;
    if (timeout_ms < 0) {
        timeout = -1;
    } else if (timeout_ms > INT_MAX) {
        timeout = INT_MAX;
    } else {
        timeout = (int)timeout_ms;
    }
    struct pollfd pfd;
    pfd.fd = (int)fd;
    pfd.events = (short)events;
    pfd.revents = 0;
    int rc = poll(&pfd, 1, timeout);
    if (rc < 0) return -1;
    if (rc == 0) return 0;
    return (pfd.revents & (events | POLLERR | POLLHUP | POLLNVAL)) != 0 ? 1 : 0;
}

PyObject *py_virtual_thread_new(PyObject *continuation) {
    PyVirtualThreadObject *vt = (PyVirtualThreadObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyVirtualThreadObject),
        PY_TYPE_VIRTUAL_THREAD,
        0
    );
    if (vt == NULL) return NULL;
    vt->continuation = NULL;
    vt->result = NULL;
    vt->state = PCC_VTHREAD_NEW;
    vt->queued = 0;
    vt->pinned = 0;
    pcc_gc_store_ptr(
        (PyObject *)vt,
        &vt->continuation,
        continuation == NULL ? py_None : continuation
    );
    py_gc_track((PyObject *)vt);
    return (PyObject *)vt;
}

int64_t py_virtual_thread_start(PyObject *vthread) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL) return -1;
    if (vt->state == PCC_VTHREAD_DONE) return -1;
    if (pcc_vthread_scheduler_init() != 0) return -1;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return -1;
    if (vt->state == PCC_VTHREAD_NEW || vt->state == PCC_VTHREAD_PARKED) {
        vt->state = PCC_VTHREAD_READY;
    }
    int rc = pcc_vthread_enqueue_locked((PyObject *)vt);
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return rc == 0 ? 0 : -1;
}

int64_t py_virtual_thread_park(PyObject *vthread) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL || vt->state == PCC_VTHREAD_DONE) return -1;
    vt->state = PCC_VTHREAD_PARKED;
    return 0;
}

int64_t py_virtual_thread_unpark(PyObject *vthread) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL || vt->state == PCC_VTHREAD_DONE) return -1;
    if (pcc_vthread_scheduler_init() != 0) return -1;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return -1;
    int rc = pcc_vthread_make_ready_locked(vt);
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return rc == 0 ? 0 : -1;
}

int64_t py_virtual_thread_sleep(PyObject *vthread, int64_t delay_ms) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL || vt->state == PCC_VTHREAD_DONE) return -1;
    if (pcc_vthread_scheduler_init() != 0) return -1;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return -1;
    int rc = 0;
    if (delay_ms <= 0) {
        rc = pcc_vthread_make_ready_locked(vt);
    } else {
        vt->state = PCC_VTHREAD_PARKED;
        rc = pcc_vthread_timer_add_locked(
            (PyObject *)vt,
            pcc_vthread_now_ms() + delay_ms
        );
    }
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return rc == 0 ? 0 : -1;
}

int64_t py_virtual_thread_poll_timers(void) {
    if (pcc_vthread_scheduler_init() != 0) return 0;
    int64_t now = pcc_vthread_now_ms();
    int64_t woken = 0;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return 0;
    PccVirtualThreadTimerEntry **cur = &pcc_vthread_timer_queue;
    while (*cur != NULL) {
        PccVirtualThreadTimerEntry *entry = *cur;
        if (entry->deadline_ms > now) break;
        *cur = entry->next;
        if (pcc_vthread_timer_count_value > 0) pcc_vthread_timer_count_value--;
        PyObject *thread = entry->thread;
        if (
            thread != NULL
            && !PY_IS_TAGGED_INT(thread)
            && py_type_of(thread) == PY_TYPE_VIRTUAL_THREAD
        ) {
            PyVirtualThreadObject *vt = (PyVirtualThreadObject *)thread;
            if (vt->state == PCC_VTHREAD_PARKED) {
                vt->queued = 0;
                if (pcc_vthread_make_ready_locked(vt) == 0) woken++;
            }
        }
        pcc_vthread_timer_entry_free(entry);
    }
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return woken;
}

int64_t py_virtual_thread_timer_count(void) {
    if (pcc_vthread_scheduler_init() != 0) return 0;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return 0;
    int64_t count = pcc_vthread_timer_count_value;
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return count;
}

int64_t py_virtual_thread_block_on_fd(
    PyObject *vthread,
    int64_t fd,
    int64_t events,
    int64_t timeout_ms
) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL || vt->state == PCC_VTHREAD_DONE) return -1;
    int ready = pcc_vthread_fd_ready(fd, events, 0);
    if (ready < 0) return -1;
    if (pcc_vthread_scheduler_init() != 0) return -1;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return -1;
    int rc = 0;
    if (ready != 0) {
        rc = pcc_vthread_make_ready_locked(vt);
        (void)pcc_mutex_unlock(pcc_vthread_lock);
        return rc == 0 ? 1 : -1;
    }
    vt->state = PCC_VTHREAD_PARKED;
    int64_t deadline = timeout_ms >= 0 ? pcc_vthread_now_ms() + timeout_ms : -1;
    rc = pcc_vthread_poll_add_locked((PyObject *)vt, fd, events, deadline);
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return rc == 0 ? 0 : -1;
}

int64_t py_virtual_thread_poll_io(int64_t timeout_ms) {
    (void)timeout_ms;
    if (pcc_vthread_scheduler_init() != 0) return 0;
    int64_t woken = 0;
    int64_t now = pcc_vthread_now_ms();
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return 0;
    PccVirtualThreadPollEntry **cur = &pcc_vthread_poll_queue;
    while (*cur != NULL) {
        PccVirtualThreadPollEntry *entry = *cur;
        int expired = entry->deadline_ms >= 0 && entry->deadline_ms <= now;
        int ready = expired ? 1 : pcc_vthread_fd_ready(entry->fd, entry->events, 0);
        if (ready <= 0) {
            cur = &(*cur)->next;
            continue;
        }
        *cur = entry->next;
        if (pcc_vthread_io_wait_count_value > 0) pcc_vthread_io_wait_count_value--;
        PyObject *thread = entry->thread;
        if (
            thread != NULL
            && !PY_IS_TAGGED_INT(thread)
            && py_type_of(thread) == PY_TYPE_VIRTUAL_THREAD
        ) {
            PyVirtualThreadObject *vt = (PyVirtualThreadObject *)thread;
            if (vt->state == PCC_VTHREAD_PARKED) {
                vt->queued = 0;
                if (pcc_vthread_make_ready_locked(vt) == 0) woken++;
            }
        }
        pcc_vthread_poll_entry_free(entry);
    }
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return woken;
}

int64_t py_virtual_thread_io_wait_count(void) {
    if (pcc_vthread_scheduler_init() != 0) return 0;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return 0;
    int64_t count = pcc_vthread_io_wait_count_value;
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return count;
}

PyObject *py_virtual_thread_poll_ready(void) {
    if (pcc_vthread_scheduler_init() != 0) return NULL;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return NULL;
    PyObject *thread = pcc_vthread_dequeue_locked();
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return thread;
}

int64_t py_virtual_thread_ready_count(void) {
    if (pcc_vthread_scheduler_init() != 0) return 0;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return 0;
    int64_t count = pcc_vthread_ready_count_value;
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return count;
}

int64_t py_virtual_thread_carrier_count(void) {
    return __atomic_load_n(&pcc_vthread_carrier_count, __ATOMIC_ACQUIRE);
}

int64_t py_virtual_thread_carrier_steal_count(void) {
    return __atomic_load_n(&pcc_vthread_carrier_steal_count, __ATOMIC_ACQUIRE);
}

typedef void (*PccVirtualThreadResumeFn)(void);
typedef int64_t (*PccVirtualThreadTypedResumeFn)(PyObject *, PyObject *);

typedef struct PccVirtualThreadCarrierPoolRun {
    int64_t max_steps;
    int64_t claimed_steps;
    int64_t ran_steps;
    int64_t failures;
} PccVirtualThreadCarrierPoolRun;

typedef struct PccVirtualThreadCarrierPoolWorker {
    PccVirtualThreadCarrierPoolRun *run;
    int64_t carrier_index;
} PccVirtualThreadCarrierPoolWorker;

int64_t py_virtual_thread_run_once(void) {
    (void)py_virtual_thread_poll_timers();
    (void)py_virtual_thread_poll_io(0);

    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == NULL) return 0;

    PyVirtualThreadObject *vt = checked_vthread(ready);
    if (vt == NULL) {
        py_decref(ready);
        return -1;
    }

    PyObject *continuation = pcc_gc_load_ptr(ready, &vt->continuation);
    if (
        continuation != NULL
        && !PY_IS_TAGGED_INT(continuation)
        && py_type_of(continuation) == PY_TYPE_CONTINUATION
    ) {
        void *resume_pc = py_continuation_resume_pc(continuation);
        if (resume_pc != NULL) {
            PyObject *saved_current = pcc_current_virtual_thread;
            pcc_current_virtual_thread = ready;
            int64_t resume_rc = 0;
            if (
                py_continuation_resume_abi(continuation)
                == PCC_CONTINUATION_RESUME_ABI_VTHREAD
            ) {
                resume_rc = ((PccVirtualThreadTypedResumeFn)resume_pc)(
                    ready,
                    continuation
                );
            } else {
                ((PccVirtualThreadResumeFn)resume_pc)();
            }
            pcc_current_virtual_thread = saved_current;
            if (resume_rc != 0) {
                py_decref(ready);
                return -1;
            }
        }
    }

    int64_t state = py_virtual_thread_state(ready);
    if (state == PCC_VTHREAD_RUNNING) {
        if (py_virtual_thread_complete(ready, py_None) != 0) {
            py_decref(ready);
            return -1;
        }
    }
    py_decref(ready);
    return 1;
}

int64_t py_virtual_thread_run_until_idle(int64_t max_steps) {
    if (max_steps <= 0) return 0;
    int64_t ran = 0;
    for (int64_t i = 0; i < max_steps; i++) {
        int64_t step = py_virtual_thread_run_once();
        if (step < 0) return -1;
        if (step == 0) break;
        ran += step;
    }
    return ran;
}

static int pcc_vthread_carrier_pool_claim(PccVirtualThreadCarrierPoolRun *run) {
    if (run == NULL) return 0;
    for (;;) {
        int64_t old = __atomic_load_n(&run->claimed_steps, __ATOMIC_ACQUIRE);
        if (old >= run->max_steps) return 0;
        if (__atomic_compare_exchange_n(
                &run->claimed_steps,
                &old,
                old + 1,
                0,
                __ATOMIC_ACQ_REL,
                __ATOMIC_ACQUIRE
            )) {
            return 1;
        }
    }
}

static void *pcc_vthread_carrier_pool_main(void *opaque) {
    PccVirtualThreadCarrierPoolWorker *worker = (
        PccVirtualThreadCarrierPoolWorker *
    )opaque;
    PccVirtualThreadCarrierPoolRun *run = worker == NULL ? NULL : worker->run;
    int64_t saved_carrier = pcc_current_virtual_thread_carrier;
    pcc_current_virtual_thread_carrier = (
        worker == NULL ? -1 : worker->carrier_index
    );
    __atomic_add_fetch(&pcc_vthread_carrier_count, 1, __ATOMIC_ACQ_REL);
    while (pcc_vthread_carrier_pool_claim(run)) {
        int64_t step = py_virtual_thread_run_once();
        if (step < 0) {
            __atomic_add_fetch(&run->failures, 1, __ATOMIC_ACQ_REL);
            break;
        }
        if (step == 0) break;
        __atomic_add_fetch(&run->ran_steps, step, __ATOMIC_ACQ_REL);
    }
    __atomic_sub_fetch(&pcc_vthread_carrier_count, 1, __ATOMIC_ACQ_REL);
    pcc_current_virtual_thread_carrier = saved_carrier;
    return NULL;
}

int64_t py_virtual_thread_run_carrier_pool(
    int64_t carrier_count,
    int64_t max_steps
) {
    if (max_steps <= 0) return 0;
    if (carrier_count <= 1 || !pcc_threads_enabled()) {
        return py_virtual_thread_run_until_idle(max_steps);
    }
    if (carrier_count > 64) carrier_count = 64;

    PccThreadHandle **handles = (
        PccThreadHandle **
    )calloc((size_t)carrier_count, sizeof(PccThreadHandle *));
    if (handles == NULL) return -1;
    PccVirtualThreadCarrierPoolWorker *workers = (
        PccVirtualThreadCarrierPoolWorker *
    )calloc((size_t)carrier_count, sizeof(PccVirtualThreadCarrierPoolWorker));
    if (workers == NULL) {
        free(handles);
        return -1;
    }

    int opened_queues = 0;
    if (pcc_vthread_scheduler_init() != 0) {
        free(workers);
        free(handles);
        return -1;
    }
    if (pcc_mutex_lock(pcc_vthread_lock) == 0) {
        if (pcc_vthread_carrier_queues == NULL) {
            if (pcc_vthread_carrier_queues_open_locked(carrier_count) == 0) {
                opened_queues = 1;
            }
        }
        (void)pcc_mutex_unlock(pcc_vthread_lock);
    }

    PccVirtualThreadCarrierPoolRun run;
    run.max_steps = max_steps;
    run.claimed_steps = 0;
    run.ran_steps = 0;
    run.failures = 0;

    int64_t started = 0;
    for (int64_t i = 0; i < carrier_count; i++) {
        workers[i].run = &run;
        workers[i].carrier_index = i;
        if (pcc_thread_start(
                &handles[i],
                pcc_vthread_carrier_pool_main,
                &workers[i]
            ) != 0) {
            __atomic_add_fetch(&run.failures, 1, __ATOMIC_ACQ_REL);
            break;
        }
        started++;
    }

    for (int64_t i = 0; i < started; i++) {
        if (pcc_thread_join(handles[i], NULL) != 0) {
            __atomic_add_fetch(&run.failures, 1, __ATOMIC_ACQ_REL);
        }
    }
    if (opened_queues && pcc_mutex_lock(pcc_vthread_lock) == 0) {
        pcc_vthread_carrier_queues_close_locked();
        (void)pcc_mutex_unlock(pcc_vthread_lock);
    }
    free(workers);
    free(handles);
    if (started == 0) return -1;
    if (__atomic_load_n(&run.failures, __ATOMIC_ACQUIRE) != 0) return -1;
    return __atomic_load_n(&run.ran_steps, __ATOMIC_ACQUIRE);
}

static void *pcc_vthread_persistent_carrier_main(void *opaque) {
    int64_t saved_carrier = pcc_current_virtual_thread_carrier;
    int64_t carrier_index = opaque == NULL ? -1 : *((int64_t *)opaque);
    pcc_current_virtual_thread_carrier = carrier_index;
    __atomic_add_fetch(&pcc_vthread_carrier_count, 1, __ATOMIC_ACQ_REL);
    while (
        __atomic_load_n(&pcc_vthread_persistent_pool_stop, __ATOMIC_ACQUIRE)
        == 0
    ) {
        int64_t step = py_virtual_thread_run_once();
        if (step < 0) {
            __atomic_add_fetch(
                &pcc_vthread_persistent_pool_failures,
                1,
                __ATOMIC_ACQ_REL
            );
            break;
        }
        if (step == 0) {
            (void)py_virtual_thread_poll_timers();
            (void)py_virtual_thread_poll_io(1);
            pcc_thread_safepoint();
            (void)poll(NULL, 0, 1);
        }
    }
    __atomic_sub_fetch(&pcc_vthread_carrier_count, 1, __ATOMIC_ACQ_REL);
    pcc_current_virtual_thread_carrier = saved_carrier;
    return NULL;
}

int64_t py_virtual_thread_carrier_pool_start(int64_t carrier_count) {
    if (carrier_count <= 0) return 0;
    if (!pcc_threads_enabled()) return 0;
    if (carrier_count > 64) carrier_count = 64;
    if (
        __atomic_load_n(
            &pcc_vthread_persistent_pool_running,
            __ATOMIC_ACQUIRE
        ) != 0
    ) {
        return __atomic_load_n(
            &pcc_vthread_persistent_carrier_count,
            __ATOMIC_ACQUIRE
        );
    }

    PccThreadHandle **handles = (
        PccThreadHandle **
    )calloc((size_t)carrier_count, sizeof(PccThreadHandle *));
    if (handles == NULL) return -1;
    int64_t *indices = (int64_t *)calloc((size_t)carrier_count, sizeof(int64_t));
    if (indices == NULL) {
        free(handles);
        return -1;
    }
    if (pcc_vthread_scheduler_init() != 0) {
        free(indices);
        free(handles);
        return -1;
    }
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) {
        free(indices);
        free(handles);
        return -1;
    }
    if (pcc_vthread_carrier_queues_open_locked(carrier_count) != 0) {
        (void)pcc_mutex_unlock(pcc_vthread_lock);
        free(indices);
        free(handles);
        return -1;
    }
    (void)pcc_mutex_unlock(pcc_vthread_lock);

    pcc_vthread_persistent_carriers = handles;
    pcc_vthread_persistent_carrier_indices = indices;
    __atomic_store_n(&pcc_vthread_persistent_pool_stop, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&pcc_vthread_persistent_pool_failures, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&pcc_vthread_persistent_carrier_count, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&pcc_vthread_persistent_pool_running, 1, __ATOMIC_RELEASE);

    int64_t started = 0;
    for (int64_t i = 0; i < carrier_count; i++) {
        indices[i] = i;
        if (pcc_thread_start(
                &handles[i],
                pcc_vthread_persistent_carrier_main,
                &indices[i]
            ) != 0) {
            break;
        }
        started++;
        __atomic_store_n(
            &pcc_vthread_persistent_carrier_count,
            started,
            __ATOMIC_RELEASE
        );
    }
    if (started == 0) {
        __atomic_store_n(&pcc_vthread_persistent_pool_running, 0, __ATOMIC_RELEASE);
        if (pcc_mutex_lock(pcc_vthread_lock) == 0) {
            pcc_vthread_carrier_queues_close_locked();
            (void)pcc_mutex_unlock(pcc_vthread_lock);
        }
        free(indices);
        free(handles);
        pcc_vthread_persistent_carrier_indices = NULL;
        pcc_vthread_persistent_carriers = NULL;
        return -1;
    }
    if (started != carrier_count) {
        __atomic_store_n(&pcc_vthread_persistent_pool_stop, 1, __ATOMIC_RELEASE);
        for (int64_t i = 0; i < started; i++) {
            (void)pcc_thread_join(handles[i], NULL);
        }
        __atomic_store_n(&pcc_vthread_persistent_pool_running, 0, __ATOMIC_RELEASE);
        __atomic_store_n(&pcc_vthread_persistent_carrier_count, 0, __ATOMIC_RELEASE);
        if (pcc_mutex_lock(pcc_vthread_lock) == 0) {
            pcc_vthread_carrier_queues_close_locked();
            (void)pcc_mutex_unlock(pcc_vthread_lock);
        }
        free(indices);
        free(handles);
        pcc_vthread_persistent_carrier_indices = NULL;
        pcc_vthread_persistent_carriers = NULL;
        return -1;
    }
    return started;
}

int64_t py_virtual_thread_carrier_pool_stop(void) {
    if (
        __atomic_load_n(
            &pcc_vthread_persistent_pool_running,
            __ATOMIC_ACQUIRE
        ) == 0
    ) {
        return 0;
    }
    __atomic_store_n(&pcc_vthread_persistent_pool_stop, 1, __ATOMIC_RELEASE);
    PccThreadHandle **handles = pcc_vthread_persistent_carriers;
    int64_t count = __atomic_load_n(
        &pcc_vthread_persistent_carrier_count,
        __ATOMIC_ACQUIRE
    );
    int64_t joined = 0;
    for (int64_t i = 0; i < count; i++) {
        if (handles != NULL && handles[i] != NULL) {
            if (pcc_thread_join(handles[i], NULL) == 0) joined++;
        }
    }
    free(handles);
    free(pcc_vthread_persistent_carrier_indices);
    pcc_vthread_persistent_carriers = NULL;
    pcc_vthread_persistent_carrier_indices = NULL;
    if (pcc_mutex_lock(pcc_vthread_lock) == 0) {
        pcc_vthread_carrier_queues_close_locked();
        (void)pcc_mutex_unlock(pcc_vthread_lock);
    }
    __atomic_store_n(&pcc_vthread_persistent_carrier_count, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&pcc_vthread_persistent_pool_running, 0, __ATOMIC_RELEASE);
    if (
        __atomic_load_n(
            &pcc_vthread_persistent_pool_failures,
            __ATOMIC_ACQUIRE
        ) != 0
    ) {
        return -1;
    }
    return joined;
}

PyObject *py_virtual_thread_current(void) {
    PyObject *current = pcc_current_virtual_thread;
    if (current == NULL) current = py_None;
    py_incref(current);
    return current;
}

int64_t py_virtual_thread_resume_generator(
    PyObject *vthread,
    PyObject *continuation
) {
    PyObject *gen = py_continuation_get_slot(continuation, 0);
    if (gen == NULL) return -1;

    PyObject *yielded = py_gen_next(gen);
    if (yielded != NULL) {
        py_decref(yielded);
        int64_t state = py_virtual_thread_state(vthread);
        int64_t rc = 0;
        if (state == PCC_VTHREAD_RUNNING) {
            rc = py_virtual_thread_unpark(vthread);
        }
        py_decref(gen);
        return rc == 0 ? 0 : -1;
    }

    PyObject *cur = py_current_exception();
    PyObject *stop_cls = (PyObject *)py_exc_builtin_class(PY_EXC_STOPITERATION);
    if (py_exc_matches(cur, stop_cls)) {
        PyObject *value = py_exc_get_message(cur);
        if (value == NULL) value = py_None;
        py_incref(value);
        py_clear_exception();
        int64_t rc = py_virtual_thread_complete(vthread, value);
        py_decref(value);
        py_decref(gen);
        return rc == 0 ? 0 : -1;
    }

    py_decref(gen);
    return -1;
}

int64_t py_virtual_thread_state(PyObject *vthread) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL) return -1;
    return vt->state;
}

int64_t py_virtual_thread_complete(PyObject *vthread, PyObject *result) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL) return -1;
    pcc_gc_store_ptr((PyObject *)vt, &vt->result, result == NULL ? py_None : result);
    vt->state = PCC_VTHREAD_DONE;
    vt->queued = 0;
    return 0;
}

PyObject *py_virtual_thread_result(PyObject *vthread) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL) return NULL;
    PyObject *result = pcc_gc_load_ptr(vthread, &vt->result);
    if (result == NULL) result = py_None;
    py_incref(result);
    return result;
}

int64_t py_virtual_thread_pin_enter(PyObject *vthread, const char *reason) {
    (void)reason;
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL || vt->state == PCC_VTHREAD_DONE) return -1;
    if (pcc_vthread_scheduler_init() != 0) return -1;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return -1;
    if (vt->pinned == 0) pcc_gc_pin((PyObject *)vt);
    vt->pinned++;
    pcc_vthread_pin_depth_total++;
    pcc_vthread_pin_events++;
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return vt->pinned;
}

int64_t py_virtual_thread_pin_leave(PyObject *vthread) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL) return -1;
    if (pcc_vthread_scheduler_init() != 0) return -1;
    if (pcc_mutex_lock(pcc_vthread_lock) != 0) return -1;
    if (vt->pinned > 0) {
        vt->pinned--;
        if (pcc_vthread_pin_depth_total > 0) pcc_vthread_pin_depth_total--;
        if (vt->pinned == 0) pcc_gc_unpin((PyObject *)vt);
    }
    int64_t pinned = vt->pinned;
    (void)pcc_mutex_unlock(pcc_vthread_lock);
    return pinned;
}

int64_t py_virtual_thread_pin_count(PyObject *vthread) {
    PyVirtualThreadObject *vt = checked_vthread(vthread);
    if (vt == NULL) return -1;
    return vt->pinned;
}

int64_t py_virtual_thread_pinned_count(void) {
    return pcc_vthread_pin_depth_total;
}

int64_t py_virtual_thread_pin_event_count(void) {
    return pcc_vthread_pin_events;
}

void py_dealloc_virtual_thread(PyObject *o) {
    PyVirtualThreadObject *vt = (PyVirtualThreadObject *)o;
    PyObject *continuation = pcc_gc_load_ptr(o, &vt->continuation);
    PyObject *result = pcc_gc_load_ptr(o, &vt->result);
    if (continuation != NULL) py_decref(continuation);
    if (result != NULL) py_decref(result);
    pcc_gc_free_object_memory(o);
}

/* DEBUG: catch py_incref called on a pointer that's not a valid PyObject. */
void pcc_debug_bad_incref(void *o, int32_t tag) {
    fprintf(stderr, "[BAD_INCREF] o=%p tag=%d\n", o, tag);
    fflush(stderr);
    __builtin_trap();
}

typedef struct PccDebugAllocEntry {
    void *ptr;
    int64_t size;
} PccDebugAllocEntry;

#define PCC_DEBUG_ALLOC_TABLE_SIZE 1048576u
#define PCC_DEBUG_ALLOC_PROBE_LIMIT 64u

static PccDebugAllocEntry *pcc_debug_alloc_table = NULL;
static int pcc_debug_runtime_enabled_cache = -1;

static int pcc_debug_runtime_enabled(void) {
    int cached = __atomic_load_n(
        &pcc_debug_runtime_enabled_cache, __ATOMIC_ACQUIRE
    );
    if (cached < 0) {
        int value = getenv("PCC_DEBUG_RUNTIME") != NULL ? 1 : 0;
        int expected = -1;
        (void)__atomic_compare_exchange_n(
            &pcc_debug_runtime_enabled_cache,
            &expected,
            value,
            0,
            __ATOMIC_ACQ_REL,
            __ATOMIC_ACQUIRE
        );
        cached = __atomic_load_n(
            &pcc_debug_runtime_enabled_cache, __ATOMIC_ACQUIRE
        );
    }
    return cached;
}

static uint64_t pcc_debug_alloc_hash(void *ptr) {
    uint64_t x = ((uint64_t)(uintptr_t)ptr) >> 4;
    x ^= x >> 33;
    x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33;
    return x & (PCC_DEBUG_ALLOC_TABLE_SIZE - 1u);
}

void pcc_debug_note_alloc_size(void *ptr, int64_t size) {
    if (!pcc_debug_runtime_enabled()) return;
    if (ptr == NULL || size <= 0) return;
    if (pcc_debug_alloc_table == NULL) {
        pcc_debug_alloc_table = (PccDebugAllocEntry *)calloc(
            PCC_DEBUG_ALLOC_TABLE_SIZE, sizeof(PccDebugAllocEntry)
        );
        if (pcc_debug_alloc_table == NULL) return;
    }
    uint64_t h = pcc_debug_alloc_hash(ptr);
    for (uint64_t n = 0; n < PCC_DEBUG_ALLOC_PROBE_LIMIT; n++) {
        PccDebugAllocEntry *entry =
            &pcc_debug_alloc_table[(h + n) & (PCC_DEBUG_ALLOC_TABLE_SIZE - 1u)];
        if (entry->ptr == NULL || entry->ptr == ptr) {
            entry->ptr = ptr;
            entry->size = size;
            return;
        }
    }
    pcc_debug_alloc_table[h].ptr = ptr;
    pcc_debug_alloc_table[h].size = size;
}

static int64_t pcc_debug_alloc_size_exact(void *ptr) {
    if (!pcc_debug_runtime_enabled()) return 0;
    if (ptr == NULL || pcc_debug_alloc_table == NULL) return 0;
    uint64_t h = pcc_debug_alloc_hash(ptr);
    for (uint64_t n = 0; n < PCC_DEBUG_ALLOC_PROBE_LIMIT; n++) {
        PccDebugAllocEntry *entry =
            &pcc_debug_alloc_table[(h + n) & (PCC_DEBUG_ALLOC_TABLE_SIZE - 1u)];
        if (entry->ptr == ptr) return entry->size;
        if (entry->ptr == NULL) return 0;
    }
    return 0;
}

static int pcc_debug_type_tag_is_valid(int32_t tag) {
    return (
        tag == PY_TYPE_NONE || tag == PY_TYPE_BOOL || tag == PY_TYPE_INT
        || tag == PY_TYPE_FLOAT || tag == PY_TYPE_STR || tag == PY_TYPE_LIST
        || tag == PY_TYPE_DICT || tag == PY_TYPE_TUPLE || tag == PY_TYPE_SET
        || tag == PY_TYPE_FUNC || tag == PY_TYPE_CLASS || tag == PY_TYPE_INSTANCE
        || tag == PY_TYPE_EXC || tag == PY_TYPE_FILE || tag == PY_TYPE_ITER
        || tag == PY_TYPE_GEN || tag == PY_TYPE_COMPLEX || tag == PY_TYPE_BYTES
        || tag == PY_TYPE_BYTEARRAY || tag == PY_TYPE_MEMORYVIEW
        || tag == PY_TYPE_COROUTINE || tag == PY_TYPE_WEAKREF
        || tag == PY_TYPE_THREAD_LOCK || tag == PY_TYPE_THREAD_RLOCK
        || tag == PY_TYPE_THREAD_EVENT || tag == PY_TYPE_THREAD_CONDITION
        || tag == PY_TYPE_THREAD_SEMAPHORE || tag == PY_TYPE_THREAD
        || tag == PY_TYPE_TASK
        || tag == PY_TYPE_CONTINUATION
        || tag == PY_TYPE_VIRTUAL_THREAD
        || tag >= PY_TYPE_USER
    );
}

static int pcc_debug_untracked_release_has_valid_header(void *obj) {
    uintptr_t p = (uintptr_t)obj;
    if (obj == NULL || (p & 1u) != 0u) return 0;
    if ((p & (sizeof(void *) - 1u)) != 0u) return 0;
    PyObjectHeader *h = py_header((PyObject *)obj);
    if (h->refcount <= 0) return 0;
    if (!pcc_debug_type_tag_is_valid(h->type_tag) || h->type_tag > 500) return 0;
    return 1;
}

int32_t pcc_debug_check_tuple_slot(
    void *tuple, int64_t i, int64_t len, void *item
) {
    if (!pcc_debug_runtime_enabled()) return 0;
    int32_t reason = 0;
    int64_t exact_size = pcc_debug_alloc_size_exact(tuple);
    int64_t slot_end = 0;
    int64_t tuple_end = 0;
    if (tuple == NULL) {
        return 0;
    }
    if (i < 0 || len < 0 || i >= len) {
        reason = 1;
    } else if (len > (9223372036854775807LL - 24) / 8) {
        reason = 2;
    } else {
        slot_end = 24 + (i + 1) * 8;
        tuple_end = 24 + len * 8;
        if (exact_size > 0 && (slot_end > exact_size || tuple_end > exact_size)) {
            reason = 3;
        }
    }
    if (reason != 0) {
        fprintf(
            stderr,
            "[BAD_TUPLE_SLOT] tuple=%p i=%lld len=%lld item=%p exact_size=%lld slot_end=%lld tuple_end=%lld reason=%d\n",
            tuple,
            (long long)i,
            (long long)len,
            item,
            (long long)exact_size,
            (long long)slot_end,
            (long long)tuple_end,
            reason
        );
        fflush(stderr);
        __builtin_trap();
    }
    return 0;
}

void pcc_debug_check_release(const char *name, void *obj) {
    if (!pcc_debug_runtime_enabled()) return;
    uintptr_t p = (uintptr_t)obj;
    if (obj == NULL) return;
    if ((p & 1u) != 0u) return;
    int64_t exact_size = pcc_debug_alloc_size_exact(obj);
    if (exact_size == 0 && p >= 0x160000000ULL && p < 0x180000000ULL) {
        if (pcc_debug_untracked_release_has_valid_header(obj)) return;
        fprintf(
            stderr,
            "[BAD_RELEASE] name=%s obj=%p exact_size=%lld reason=stack-looking-pointer\n",
            name != NULL ? name : "<null>",
            obj,
            (long long)exact_size
        );
        fflush(stderr);
        __builtin_trap();
    }
    if (exact_size > 0) {
        PyObjectHeader *h = py_header((PyObject *)obj);
        if (
            h->refcount <= 0
            || !pcc_debug_type_tag_is_valid(h->type_tag)
            || h->type_tag > 500
        ) {
            fprintf(
                stderr,
                "[BAD_RELEASE] name=%s obj=%p exact_size=%lld refcount=%lld tag=%d flags=%d reason=bad-header\n",
                name != NULL ? name : "<null>",
                obj,
                (long long)exact_size,
                (long long)h->refcount,
                h->type_tag,
                h->flags
            );
            fflush(stderr);
            __builtin_trap();
        }
    }
}

void pcc_debug_bad_str_concat(
    void *a, void *b, int64_t tag_a, int64_t tag_b
) {
    if (!pcc_debug_runtime_enabled()) return;
    fprintf(
        stderr,
        "[BAD_STR_CONCAT] a=%p b=%p tag_a=%lld tag_b=%lld\n",
        a,
        b,
        (long long)tag_a,
        (long long)tag_b
    );
    fflush(stderr);
    __builtin_trap();
}
