/* Native threading module support.
 *
 * This file intentionally exposes only small runtime objects.  It is the
 * bridge from Python-visible threading shims to the single shared substrate in
 * pcc_threads.c.  PCC_WITH_THREADS=0 keeps behavior deterministic: locks are
 * no-op mutex wrappers and Thread.start runs the target synchronously.  When
 * PCC_WITH_THREADS=1, Thread.start hands the object to the pthread substrate
 * and protects that handoff with an owned runtime reference.
 */
#include "py_internal.h"
#include <stdlib.h>
#include <stdint.h>

#ifndef PCC_WITH_THREADS
#define PCC_WITH_THREADS 0
#endif

#if PCC_WITH_THREADS
#include <pthread.h>
#endif

typedef struct PyThreadVThreadWaiter {
    PyObject *vthread;
    struct PyThreadVThreadWaiter *next;
    void *root_handle;
    int pool_entry;
} PyThreadVThreadWaiter;

/* Bounded slab/freelist pool for vthread waiter nodes.
 *
 * This mirrors PCC_VTHREAD_READY_ENTRY_POOL_LIMIT in pcc_threads.c: hot
 * lock/event/condition/semaphore contention repeatedly enqueues and pops
 * waiter nodes, and raw calloc/free per park churns the allocator.  Recycling
 * nodes through a bounded freelist removes that churn while keeping the same
 * observable semantics (mutual exclusion, wake order, no node leaks).
 *
 * Unlike the ready-entry pool -- which is protected by the single scheduler
 * lock (pcc_vthread_lock) -- waiter nodes are enqueued/popped while a
 * per-object mutex is held, and different lock/event objects hold different
 * mutexes.  The shared freelist therefore needs its own dedicated guard so
 * concurrent operations on distinct sync objects cannot corrupt it.  Under
 * PCC_WITH_THREADS=0 the guard compiles to nothing. */
#define PCC_VTHREAD_WAITER_POOL_LIMIT 4096

static PyThreadVThreadWaiter *pcc_vthread_waiter_free_head = NULL;
static int64_t pcc_vthread_waiter_free_count = 0;

#if PCC_WITH_THREADS
static pthread_mutex_t pcc_vthread_waiter_pool_lock = PTHREAD_MUTEX_INITIALIZER;
static inline void pcc_vthread_waiter_pool_lock_acquire(void) {
    pthread_mutex_lock(&pcc_vthread_waiter_pool_lock);
}
static inline void pcc_vthread_waiter_pool_lock_release(void) {
    pthread_mutex_unlock(&pcc_vthread_waiter_pool_lock);
}
#else
static inline void pcc_vthread_waiter_pool_lock_acquire(void) {}
static inline void pcc_vthread_waiter_pool_lock_release(void) {}
#endif

static void pcc_vthread_waiter_clear(PyThreadVThreadWaiter *entry) {
    if (entry == NULL) return;
    entry->vthread = NULL;
    entry->next = NULL;
    entry->root_handle = NULL;
    entry->pool_entry = 0;
}

/* Take a zeroed waiter node, reusing a pooled one when available. */
static PyThreadVThreadWaiter *pcc_vthread_waiter_alloc(void) {
    PyThreadVThreadWaiter *entry = NULL;
    pcc_vthread_waiter_pool_lock_acquire();
    if (pcc_vthread_waiter_free_head != NULL) {
        entry = pcc_vthread_waiter_free_head;
        pcc_vthread_waiter_free_head = entry->next;
        pcc_vthread_waiter_pool_note_reuse();
        if (pcc_vthread_waiter_free_count > 0) {
            pcc_vthread_waiter_free_count--;
        }
        pcc_vthread_waiter_pool_note_cached(pcc_vthread_waiter_free_count);
    }
    pcc_vthread_waiter_pool_lock_release();
    if (entry == NULL) {
        entry = (PyThreadVThreadWaiter *)malloc(
            sizeof(PyThreadVThreadWaiter)
        );
        if (entry != NULL) pcc_vthread_waiter_pool_note_allocation();
    }
    pcc_vthread_waiter_clear(entry);
    return entry;
}

/* Return a waiter node to the pool, or free it when the pool is full. */
static void pcc_vthread_waiter_recycle(PyThreadVThreadWaiter *entry) {
    if (entry == NULL) return;
    pcc_vthread_waiter_clear(entry);
    pcc_vthread_waiter_pool_lock_acquire();
    if (pcc_vthread_waiter_free_count >= PCC_VTHREAD_WAITER_POOL_LIMIT) {
        pcc_vthread_waiter_pool_lock_release();
        free(entry);
        return;
    }
    entry->pool_entry = 1;
    entry->next = pcc_vthread_waiter_free_head;
    pcc_vthread_waiter_free_head = entry;
    pcc_vthread_waiter_free_count++;
    pcc_vthread_waiter_pool_note_cached(pcc_vthread_waiter_free_count);
    pcc_vthread_waiter_pool_lock_release();
}

typedef struct {
    PyObjectHeader h;
    PccMutex *mutex;
    PccCond *cond;
    int64_t held;
    PyThreadVThreadWaiter *waiters;
    PyThreadVThreadWaiter *wait_tail;
} PyThreadLockObject;

typedef struct {
    PyObjectHeader h;
    PccMutex *mutex;
    int64_t owner;
    int64_t depth;
} PyThreadRLockObject;

typedef struct {
    PyObjectHeader h;
    PccMutex *mutex;
    PccCond *cond;
    int64_t flag;
    PyThreadVThreadWaiter *waiters;
    PyThreadVThreadWaiter *wait_tail;
} PyThreadEventObject;

typedef struct {
    PyObjectHeader h;
    PccMutex *mutex;
    PccCond *cond;
    int64_t value;
    PyThreadVThreadWaiter *waiters;
    PyThreadVThreadWaiter *wait_tail;
} PyThreadSemaphoreObject;

typedef struct {
    PyObjectHeader h;
    PccThreadHandle *handle;
    PyObject *callable;
    PyObject *args;
    PyObject *result;
    int64_t started;
    int64_t joined;
    int64_t finished;
} PyThreadObject;

typedef struct {
    PyObjectHeader h;
    PccMutex *mutex;
    PccCond *cond;
    PyThreadVThreadWaiter *waiters;
    PyThreadVThreadWaiter *wait_tail;
} PyThreadConditionObject;

static int py_threading_vthread_waiter_enqueue(
    PyThreadVThreadWaiter **head,
    PyThreadVThreadWaiter **tail,
    PyObject *vthread
) {
    if (vthread == NULL || vthread == py_None) return -1;
    PyThreadVThreadWaiter *entry = pcc_vthread_waiter_alloc();
    if (entry == NULL) return -1;
    entry->root_handle = pcc_gc_scheduler_root_register_handle(&entry->vthread);
    if (entry->root_handle == NULL) {
        pcc_vthread_waiter_recycle(entry);
        return -1;
    }
    pcc_vthread_effect_note_waiter_root_enter();
    pcc_gc_store_root(&entry->vthread, vthread);
    if (*tail == NULL) {
        *head = entry;
        *tail = entry;
    } else {
        (*tail)->next = entry;
        *tail = entry;
    }
    return 0;
}

static PyObject *py_threading_vthread_waiter_pop(
    PyThreadVThreadWaiter **head,
    PyThreadVThreadWaiter **tail
) {
    while (*head != NULL) {
        PyThreadVThreadWaiter *entry = *head;
        *head = entry->next;
        if (*head == NULL) *tail = NULL;
        PyObject *vthread = pcc_gc_load_ptr(NULL, &entry->vthread);
        py_incref(vthread);
        pcc_gc_scheduler_root_unregister_handle(entry->root_handle);
        pcc_vthread_effect_note_waiter_root_leave();
        entry->root_handle = NULL;
        pcc_gc_store_root(&entry->vthread, NULL);
        pcc_vthread_waiter_recycle(entry);
        if (vthread != NULL && vthread != py_None) return vthread;
        py_decref(vthread);
    }
    return NULL;
}

static int64_t py_threading_vthread_wake_one(
    PyThreadVThreadWaiter **head,
    PyThreadVThreadWaiter **tail
) {
    PyObject *vthread = py_threading_vthread_waiter_pop(head, tail);
    if (vthread == NULL) return 0;
    int64_t rc = py_virtual_thread_unpark(vthread);
    py_decref(vthread);
    return rc == 0 ? 1 : -1;
}

static int64_t py_threading_vthread_wake_all(
    PyThreadVThreadWaiter **head,
    PyThreadVThreadWaiter **tail
) {
    int64_t woken = 0;
    for (;;) {
        PyObject *vthread = py_threading_vthread_waiter_pop(head, tail);
        if (vthread == NULL) break;
        if (py_virtual_thread_unpark(vthread) == 0) woken++;
        py_decref(vthread);
    }
    return woken;
}

static void py_threading_vthread_waiters_clear(
    PyThreadVThreadWaiter **head,
    PyThreadVThreadWaiter **tail
) {
    for (;;) {
        PyObject *vthread = py_threading_vthread_waiter_pop(head, tail);
        if (vthread == NULL) break;
        py_decref(vthread);
    }
}

static PyObject *py_threading_pin_current_vthread(const char *reason) {
    PyObject *vt = py_virtual_thread_current();
    if (vt == NULL || vt == py_None) {
        py_decref(vt);
        return NULL;
    }
    if (py_virtual_thread_pin_enter(vt, reason) < 0) {
        py_decref(vt);
        return NULL;
    }
    return vt;
}

static void py_threading_unpin_current_vthread(PyObject *vt) {
    if (vt == NULL) return;
    (void)py_virtual_thread_pin_leave(vt);
    py_decref(vt);
}

int64_t py_threading_get_ident(void) {
    return pcc_current_thread_id();
}

PyObject *py_threading_current_thread(void) {
    return py_int_from_i64(pcc_current_thread_id());
}

PyObject *py_threading_lock_new(void) {
    PyThreadLockObject *o = (PyThreadLockObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyThreadLockObject), PY_TYPE_THREAD_LOCK, 0
    );
    if (o == NULL) return NULL;
    o->mutex = pcc_mutex_new();
    o->cond = pcc_cond_new();
    if (o->mutex == NULL || o->cond == NULL) {
        pcc_cond_free(o->cond);
        pcc_mutex_free(o->mutex);
        pcc_gc_free_object_memory((PyObject *)o);
        return NULL;
    }
    o->held = 0;
    o->waiters = NULL;
    o->wait_tail = NULL;
    return (PyObject *)o;
}

int64_t py_threading_lock_acquire(PyObject *lock) {
    if (lock == NULL || PY_IS_TAGGED_INT(lock)) return -1;
    PyThreadLockObject *o = (PyThreadLockObject *)lock;
    PyObject *vt = py_threading_pin_current_vthread("threading.Lock.acquire");
    int64_t rc = pcc_mutex_lock(o->mutex);
    while (rc == 0 && o->held != 0) {
        /* Bounded wait + safepoint: a thread blocked here on a contended Lock
         * must still be able to park during stop-the-world GC. An unbounded
         * pcc_cond_wait deadlocks against a concurrent gc.collect() whose STW
         * is waiting for this thread, while the lock owner is itself parked at
         * a safepoint and cannot release. See
         * docs/investigations/pcc1-threaded-explicit-gc-backend0-double-free-highscale.md */
        int64_t w = pcc_cond_timedwait_ms(o->cond, o->mutex, 5);
        if (w < 0) { rc = -1; break; }
        /* Drop o->mutex before the safepoint. pcc_cond_timedwait_ms re-acquires
         * the mutex on return, so parking here while holding it would block
         * other waiters' cond re-acquire (which is not a safepoint) and the
         * lock releaser, re-deadlocking stop-the-world. */
        (void)pcc_mutex_unlock(o->mutex);
        pcc_thread_safepoint();
        rc = pcc_mutex_lock(o->mutex);
    }
    if (rc == 0) o->held = 1;
    if (pcc_mutex_unlock(o->mutex) != 0 && rc == 0) rc = -1;
    py_threading_unpin_current_vthread(vt);
    return rc;
}

int64_t py_threading_lock_acquire_vthread(PyObject *lock) {
    if (lock == NULL || PY_IS_TAGGED_INT(lock)) return -1;
    PyThreadLockObject *o = (PyThreadLockObject *)lock;
    PyObject *vt = py_virtual_thread_current();
    if (vt == NULL || vt == py_None) {
        py_decref(vt);
        return py_threading_lock_acquire(lock);
    }
    if (pcc_mutex_lock(o->mutex) != 0) {
        py_decref(vt);
        return -1;
    }
    if (o->held == 0) {
        o->held = 1;
        (void)pcc_mutex_unlock(o->mutex);
        py_decref(vt);
        return 0;
    }
    if (py_threading_vthread_waiter_enqueue(&o->waiters, &o->wait_tail, vt) != 0) {
        (void)pcc_mutex_unlock(o->mutex);
        py_decref(vt);
        return -1;
    }
    int64_t rc = py_virtual_thread_park(vt);
    (void)pcc_mutex_unlock(o->mutex);
    py_decref(vt);
    return rc == 0 ? 1 : -1;
}

int64_t py_threading_lock_release(PyObject *lock) {
    if (lock == NULL || PY_IS_TAGGED_INT(lock)) return -1;
    PyThreadLockObject *o = (PyThreadLockObject *)lock;
    if (pcc_mutex_lock(o->mutex) != 0) return -1;
    if (o->held == 0) {
        (void)pcc_mutex_unlock(o->mutex);
        return -1;
    }
    int64_t woken = py_threading_vthread_wake_one(&o->waiters, &o->wait_tail);
    if (woken < 0) {
        (void)pcc_mutex_unlock(o->mutex);
        return -1;
    }
    if (woken == 0) {
        o->held = 0;
        (void)pcc_cond_signal(o->cond);
    } else {
        o->held = 1;
    }
    return pcc_mutex_unlock(o->mutex);
}

void py_dealloc_thread_lock(PyObject *lock) {
    if (lock == NULL || PY_IS_TAGGED_INT(lock)) return;
    PyThreadLockObject *o = (PyThreadLockObject *)lock;
    py_threading_vthread_waiters_clear(&o->waiters, &o->wait_tail);
    pcc_cond_free(o->cond);
    pcc_mutex_free(o->mutex);
    pcc_gc_free_object_memory(lock);
}

PyObject *py_threading_rlock_new(void) {
    PyThreadRLockObject *o = (PyThreadRLockObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyThreadRLockObject), PY_TYPE_THREAD_RLOCK, 0
    );
    if (o == NULL) return NULL;
    o->mutex = pcc_mutex_new();
    if (o->mutex == NULL) {
        pcc_gc_free_object_memory((PyObject *)o);
        return NULL;
    }
    o->owner = 0;
    o->depth = 0;
    return (PyObject *)o;
}

int64_t py_threading_rlock_acquire(PyObject *lock) {
    if (lock == NULL || PY_IS_TAGGED_INT(lock)) return -1;
    PyThreadRLockObject *o = (PyThreadRLockObject *)lock;
    int64_t self = pcc_current_thread_id();
    if (o->owner == self) {
        o->depth++;
        return 0;
    }
    PyObject *vt = py_threading_pin_current_vthread("threading.RLock.acquire");
    if (pcc_mutex_lock(o->mutex) != 0) {
        py_threading_unpin_current_vthread(vt);
        return -1;
    }
    py_threading_unpin_current_vthread(vt);
    o->owner = self;
    o->depth = 1;
    return 0;
}

int64_t py_threading_rlock_release(PyObject *lock) {
    if (lock == NULL || PY_IS_TAGGED_INT(lock)) return -1;
    PyThreadRLockObject *o = (PyThreadRLockObject *)lock;
    int64_t self = pcc_current_thread_id();
    if (o->owner != self || o->depth <= 0) return -1;
    o->depth--;
    if (o->depth == 0) {
        o->owner = 0;
        return pcc_mutex_unlock(o->mutex);
    }
    return 0;
}

void py_dealloc_thread_rlock(PyObject *lock) {
    if (lock == NULL || PY_IS_TAGGED_INT(lock)) return;
    PyThreadRLockObject *o = (PyThreadRLockObject *)lock;
    pcc_mutex_free(o->mutex);
    pcc_gc_free_object_memory(lock);
}

PyObject *py_threading_event_new(void) {
    PyThreadEventObject *o = (PyThreadEventObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyThreadEventObject), PY_TYPE_THREAD_EVENT, 0
    );
    if (o == NULL) return NULL;
    o->mutex = pcc_mutex_new();
    o->cond = pcc_cond_new();
    if (o->mutex == NULL || o->cond == NULL) {
        pcc_mutex_free(o->mutex);
        pcc_cond_free(o->cond);
        pcc_gc_free_object_memory((PyObject *)o);
        return NULL;
    }
    o->flag = 0;
    o->waiters = NULL;
    o->wait_tail = NULL;
    return (PyObject *)o;
}

int64_t py_threading_event_set(PyObject *event) {
    if (event == NULL || PY_IS_TAGGED_INT(event)) return -1;
    PyThreadEventObject *o = (PyThreadEventObject *)event;
    if (pcc_mutex_lock(o->mutex) != 0) return -1;
    o->flag = 1;
    (void)py_threading_vthread_wake_all(&o->waiters, &o->wait_tail);
    (void)pcc_cond_broadcast(o->cond);
    return pcc_mutex_unlock(o->mutex);
}

int64_t py_threading_event_clear(PyObject *event) {
    if (event == NULL || PY_IS_TAGGED_INT(event)) return -1;
    PyThreadEventObject *o = (PyThreadEventObject *)event;
    if (pcc_mutex_lock(o->mutex) != 0) return -1;
    o->flag = 0;
    return pcc_mutex_unlock(o->mutex);
}

int64_t py_threading_event_is_set(PyObject *event) {
    if (event == NULL || PY_IS_TAGGED_INT(event)) return 0;
    PyThreadEventObject *o = (PyThreadEventObject *)event;
    return o->flag != 0 ? 1 : 0;
}

int64_t py_threading_event_wait(PyObject *event) {
    if (event == NULL || PY_IS_TAGGED_INT(event)) return -1;
    PyThreadEventObject *o = (PyThreadEventObject *)event;
    if (pcc_mutex_lock(o->mutex) != 0) return -1;
    PyObject *vt = py_threading_pin_current_vthread("threading.Event.wait");
    while (o->flag == 0) {
        /* Bounded wait + safepoint so an Event.wait()-blocked thread can park
         * during stop-the-world GC (same deadlock class as Lock.acquire). */
        int64_t w = pcc_cond_timedwait_ms(o->cond, o->mutex, 5);
        if (w < 0) break;
        /* Drop o->mutex before the safepoint (see Lock.acquire note). */
        (void)pcc_mutex_unlock(o->mutex);
        pcc_thread_safepoint();
        if (pcc_mutex_lock(o->mutex) != 0) break;
    }
    py_threading_unpin_current_vthread(vt);
    return pcc_mutex_unlock(o->mutex);
}

int64_t py_threading_event_wait_vthread(PyObject *event) {
    if (event == NULL || PY_IS_TAGGED_INT(event)) return -1;
    PyThreadEventObject *o = (PyThreadEventObject *)event;
    PyObject *vt = py_virtual_thread_current();
    if (vt == NULL || vt == py_None) {
        py_decref(vt);
        return py_threading_event_wait(event);
    }
    if (pcc_mutex_lock(o->mutex) != 0) {
        py_decref(vt);
        return -1;
    }
    if (o->flag != 0) {
        (void)pcc_mutex_unlock(o->mutex);
        py_decref(vt);
        return 0;
    }
    if (py_threading_vthread_waiter_enqueue(&o->waiters, &o->wait_tail, vt) != 0) {
        (void)pcc_mutex_unlock(o->mutex);
        py_decref(vt);
        return -1;
    }
    int64_t rc = py_virtual_thread_park(vt);
    (void)pcc_mutex_unlock(o->mutex);
    py_decref(vt);
    return rc == 0 ? 1 : -1;
}

void py_dealloc_thread_event(PyObject *event) {
    if (event == NULL || PY_IS_TAGGED_INT(event)) return;
    PyThreadEventObject *o = (PyThreadEventObject *)event;
    py_threading_vthread_waiters_clear(&o->waiters, &o->wait_tail);
    pcc_cond_free(o->cond);
    pcc_mutex_free(o->mutex);
    pcc_gc_free_object_memory(event);
}

PyObject *py_threading_condition_new(PyObject *lock) {
    (void)lock;
    PyThreadConditionObject *o = (PyThreadConditionObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyThreadConditionObject), PY_TYPE_THREAD_CONDITION, 0
    );
    if (o == NULL) return NULL;
    o->mutex = pcc_mutex_new();
    o->cond = pcc_cond_new();
    if (o->mutex == NULL || o->cond == NULL) {
        pcc_mutex_free(o->mutex);
        pcc_cond_free(o->cond);
        pcc_gc_free_object_memory((PyObject *)o);
        return NULL;
    }
    o->waiters = NULL;
    o->wait_tail = NULL;
    return (PyObject *)o;
}

int64_t py_threading_condition_acquire(PyObject *cond) {
    if (cond == NULL || PY_IS_TAGGED_INT(cond)) return -1;
    PyThreadConditionObject *o = (PyThreadConditionObject *)cond;
    PyObject *vt = py_threading_pin_current_vthread("threading.Condition.acquire");
    int64_t rc = pcc_mutex_lock(o->mutex);
    py_threading_unpin_current_vthread(vt);
    return rc;
}

int64_t py_threading_condition_release(PyObject *cond) {
    if (cond == NULL || PY_IS_TAGGED_INT(cond)) return -1;
    PyThreadConditionObject *o = (PyThreadConditionObject *)cond;
    return pcc_mutex_unlock(o->mutex);
}

int64_t py_threading_condition_wait(PyObject *cond) {
    if (cond == NULL || PY_IS_TAGGED_INT(cond)) return -1;
    PyThreadConditionObject *o = (PyThreadConditionObject *)cond;
    PyObject *vt = py_threading_pin_current_vthread("threading.Condition.wait");
    int64_t rc = pcc_cond_wait(o->cond, o->mutex);
    py_threading_unpin_current_vthread(vt);
    return rc;
}

int64_t py_threading_condition_wait_vthread(PyObject *cond) {
    if (cond == NULL || PY_IS_TAGGED_INT(cond)) return -1;
    PyThreadConditionObject *o = (PyThreadConditionObject *)cond;
    PyObject *vt = py_virtual_thread_current();
    if (vt == NULL || vt == py_None) {
        py_decref(vt);
        return py_threading_condition_wait(cond);
    }
    if (py_threading_vthread_waiter_enqueue(&o->waiters, &o->wait_tail, vt) != 0) {
        py_decref(vt);
        return -1;
    }
    int64_t rc = py_virtual_thread_park(vt);
    if (pcc_mutex_unlock(o->mutex) != 0 && rc == 0) rc = -1;
    py_decref(vt);
    return rc == 0 ? 1 : -1;
}

int64_t py_threading_condition_notify(PyObject *cond) {
    if (cond == NULL || PY_IS_TAGGED_INT(cond)) return -1;
    PyThreadConditionObject *o = (PyThreadConditionObject *)cond;
    int64_t woken = py_threading_vthread_wake_one(&o->waiters, &o->wait_tail);
    if (woken < 0) return -1;
    return pcc_cond_signal(o->cond);
}

void py_dealloc_thread_condition(PyObject *cond) {
    if (cond == NULL || PY_IS_TAGGED_INT(cond)) return;
    PyThreadConditionObject *o = (PyThreadConditionObject *)cond;
    py_threading_vthread_waiters_clear(&o->waiters, &o->wait_tail);
    pcc_cond_free(o->cond);
    pcc_mutex_free(o->mutex);
    pcc_gc_free_object_memory(cond);
}

PyObject *py_threading_semaphore_new(int64_t initial) {
    if (initial < 0) initial = 0;
    PyThreadSemaphoreObject *o = (PyThreadSemaphoreObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyThreadSemaphoreObject), PY_TYPE_THREAD_SEMAPHORE, 0
    );
    if (o == NULL) return NULL;
    o->mutex = pcc_mutex_new();
    o->cond = pcc_cond_new();
    if (o->mutex == NULL || o->cond == NULL) {
        pcc_mutex_free(o->mutex);
        pcc_cond_free(o->cond);
        pcc_gc_free_object_memory((PyObject *)o);
        return NULL;
    }
    o->value = initial;
    o->waiters = NULL;
    o->wait_tail = NULL;
    return (PyObject *)o;
}

int64_t py_threading_semaphore_acquire(PyObject *sem) {
    if (sem == NULL || PY_IS_TAGGED_INT(sem)) return -1;
    PyThreadSemaphoreObject *o = (PyThreadSemaphoreObject *)sem;
    if (pcc_mutex_lock(o->mutex) != 0) return -1;
    PyObject *vt = py_threading_pin_current_vthread("threading.Semaphore.acquire");
    while (o->value <= 0) {
        if (pcc_cond_wait(o->cond, o->mutex) != 0) break;
    }
    if (o->value > 0) o->value--;
    py_threading_unpin_current_vthread(vt);
    return pcc_mutex_unlock(o->mutex);
}

int64_t py_threading_semaphore_acquire_vthread(PyObject *sem) {
    if (sem == NULL || PY_IS_TAGGED_INT(sem)) return -1;
    PyThreadSemaphoreObject *o = (PyThreadSemaphoreObject *)sem;
    PyObject *vt = py_virtual_thread_current();
    if (vt == NULL || vt == py_None) {
        py_decref(vt);
        return py_threading_semaphore_acquire(sem);
    }
    if (pcc_mutex_lock(o->mutex) != 0) {
        py_decref(vt);
        return -1;
    }
    if (o->value > 0) {
        o->value--;
        (void)pcc_mutex_unlock(o->mutex);
        py_decref(vt);
        return 0;
    }
    if (py_threading_vthread_waiter_enqueue(&o->waiters, &o->wait_tail, vt) != 0) {
        (void)pcc_mutex_unlock(o->mutex);
        py_decref(vt);
        return -1;
    }
    int64_t rc = py_virtual_thread_park(vt);
    (void)pcc_mutex_unlock(o->mutex);
    py_decref(vt);
    return rc == 0 ? 1 : -1;
}

int64_t py_threading_semaphore_release(PyObject *sem) {
    if (sem == NULL || PY_IS_TAGGED_INT(sem)) return -1;
    PyThreadSemaphoreObject *o = (PyThreadSemaphoreObject *)sem;
    if (pcc_mutex_lock(o->mutex) != 0) return -1;
    int64_t woken = py_threading_vthread_wake_one(&o->waiters, &o->wait_tail);
    if (woken < 0) {
        (void)pcc_mutex_unlock(o->mutex);
        return -1;
    }
    if (woken == 0) o->value++;
    (void)pcc_cond_signal(o->cond);
    return pcc_mutex_unlock(o->mutex);
}

void py_dealloc_thread_semaphore(PyObject *sem) {
    if (sem == NULL || PY_IS_TAGGED_INT(sem)) return;
    PyThreadSemaphoreObject *o = (PyThreadSemaphoreObject *)sem;
    py_threading_vthread_waiters_clear(&o->waiters, &o->wait_tail);
    pcc_cond_free(o->cond);
    pcc_mutex_free(o->mutex);
    pcc_gc_free_object_memory(sem);
}

static void py_threading_thread_invoke(PyThreadObject *t) {
    if (t == NULL) return;
    if (t->finished) return;
    PyObject *callable = pcc_gc_load_ptr((PyObject *)t, &t->callable);
    PyObject *args = pcc_gc_load_ptr((PyObject *)t, &t->args);
    if (callable != NULL && callable != py_None) {
        PyObject *result = py_obj_call(callable, args, NULL);
        pcc_gc_store_ptr((PyObject *)t, &t->result, result);
        py_decref(result);
    } else {
        pcc_gc_store_ptr((PyObject *)t, &t->result, py_None);
    }
    t->finished = 1;
}

static void *py_threading_thread_main(void *arg) {
    PyThreadObject *t = (PyThreadObject *)arg;
    if (t == NULL) return NULL;
    /* Release the start-handoff reference acquired before pthread_create().
     * Save the result first: this decref may free the Thread wrapper if the
     * user dropped their last Python-visible reference immediately after
     * start(). */
    py_threading_thread_invoke(t);
    void *result = (void *)pcc_gc_load_ptr((PyObject *)t, &t->result);
    py_decref((PyObject *)t);
    return result;
}

PyObject *py_threading_thread_new(PyObject *callable, PyObject *args) {
    PyThreadObject *t = (PyThreadObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyThreadObject), PY_TYPE_THREAD, 0
    );
    if (t == NULL) return NULL;
    PyObject *callable_value = callable == NULL ? py_None : callable;
    PyObject *args_value = args == NULL ? py_None : args;
    t->handle = NULL;
    t->callable = NULL;
    t->args = NULL;
    t->result = NULL;
    t->started = 0;
    t->joined = 0;
    t->finished = 0;
    pcc_gc_store_ptr((PyObject *)t, &t->callable, callable_value);
    pcc_gc_store_ptr((PyObject *)t, &t->args, args_value);
    return (PyObject *)t;
}

int64_t py_threading_thread_start(PyObject *thread) {
    if (thread == NULL || PY_IS_TAGGED_INT(thread)) return -1;
    if (py_header(thread)->type_tag != PY_TYPE_THREAD) return -1;
    PyThreadObject *t = (PyThreadObject *)thread;
    if (t->started) return -1;
    if (!pcc_threads_enabled()) {
        /* Deterministic single-thread fallback: run the target now.  This
         * mirrors concurrent.futures' existing sequential fallback and keeps
         * simple Thread programs usable in the default runtime. */
        py_threading_thread_invoke(t);
        t->started = 1;
        t->joined = 1;
        return 0;
    }
    /* The child thread receives a borrowed C pointer.  Take an owned handoff
     * reference before pthread_create so `t = Thread(...); t.start(); t = None`
     * cannot free the wrapper before the trampoline enters
     * py_threading_thread_main().  The thread body releases this reference. */
    py_incref((PyObject *)t);
    if (pcc_thread_start(&t->handle, py_threading_thread_main, t) != 0) {
        py_decref((PyObject *)t);
        return -1;
    }
    t->started = 1;
    return 0;
}

int64_t py_threading_thread_join(PyObject *thread) {
    if (thread == NULL || PY_IS_TAGGED_INT(thread)) return -1;
    if (py_header(thread)->type_tag != PY_TYPE_THREAD) return -1;
    PyThreadObject *t = (PyThreadObject *)thread;
    if (!t->started) return -1;
    if (t->joined) return 0;
    if (t->handle != NULL) {
        void *result = NULL;
        PyObject *vt = py_threading_pin_current_vthread("threading.Thread.join");
        int64_t rc = pcc_thread_join(t->handle, &result);
        py_threading_unpin_current_vthread(vt);
        if (rc != 0) return -1;
        t->handle = NULL;
        (void)result;
    }
    t->joined = 1;
    return 0;
}

int64_t py_threading_thread_is_alive(PyObject *thread) {
    if (thread == NULL || PY_IS_TAGGED_INT(thread)) return 0;
    if (py_header(thread)->type_tag != PY_TYPE_THREAD) return 0;
    PyThreadObject *t = (PyThreadObject *)thread;
    return (t->started != 0 && t->joined == 0 && t->finished == 0) ? 1 : 0;
}

void py_dealloc_thread_thread(PyObject *thread) {
    if (thread == NULL || PY_IS_TAGGED_INT(thread)) return;
    PyThreadObject *t = (PyThreadObject *)thread;
    if (t->handle != NULL && !t->joined) {
        pcc_thread_detach(t->handle);
        t->handle = NULL;
    }
    PyObject *callable = pcc_gc_load_ptr(thread, &t->callable);
    PyObject *args = pcc_gc_load_ptr(thread, &t->args);
    PyObject *result = pcc_gc_load_ptr(thread, &t->result);
    py_decref(callable);
    py_decref(args);
    py_decref(result);
    pcc_gc_free_object_memory(thread);
}
