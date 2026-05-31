/* pcc fake-libc CPython pythread.h: the PyThread lock + TSS API surface that
 * numpy's Cython-generated modules (numpy.random _philox/bit_generator/...) and
 * a few _core files reference. Declarations only — the no-libpython runtime
 * provides/no-ops the implementations. Part of the pcc C-API include set copied
 * into the per-build pcc-capi-include dir (see build_exec _PCC_CAPI_HEADERS). */
#ifndef Py_PYTHREAD_H
#define Py_PYTHREAD_H

#include <stddef.h>

typedef void *PyThread_type_lock;

#define WAIT_LOCK 1
#define NOWAIT_LOCK 0

/* PyLockStatus is provided by Python.h (always included before pythread.h in
 * CPython/numpy usage); do not redefine it here. */

void PyThread_init_thread(void);
unsigned long PyThread_start_new_thread(void (*)(void *), void *);
void PyThread_exit_thread(void);
unsigned long PyThread_get_thread_ident(void);

PyThread_type_lock PyThread_allocate_lock(void);
void PyThread_free_lock(PyThread_type_lock);
int PyThread_acquire_lock(PyThread_type_lock, int);
PyLockStatus PyThread_acquire_lock_timed(PyThread_type_lock, long long, int);
void PyThread_release_lock(PyThread_type_lock);

size_t PyThread_get_stacksize(void);
int PyThread_set_stacksize(size_t);

/* Thread Specific Storage (TSS) API. */
typedef struct _Py_tss_t {
    int _is_initialized;
    unsigned long _key;
} Py_tss_t;

#define Py_tss_NEEDS_INIT {0}

Py_tss_t *PyThread_tss_alloc(void);
void PyThread_tss_free(Py_tss_t *key);
int PyThread_tss_is_created(Py_tss_t *key);
int PyThread_tss_create(Py_tss_t *key);
void PyThread_tss_delete(Py_tss_t *key);
int PyThread_tss_set(Py_tss_t *key, void *value);
void *PyThread_tss_get(Py_tss_t *key);

#endif /* Py_PYTHREAD_H */
