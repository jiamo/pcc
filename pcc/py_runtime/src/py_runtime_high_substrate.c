/* C-hosted helpers for pcc-Python runtime-high modules.
 *
 * The pcc-Python frontend can call these functions through extern(), but it
 * does not yet expose native thread-local globals or atomic memory operations
 * directly. Keep these helpers in a standalone object so
 * libpy_runtime_pcc_py.a can include them without also linking the C
 * py_substrate.o symbols that py_substrate.py replaces.
 */
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "py_internal.h"

#ifndef PCC_WITH_THREADS
#define PCC_WITH_THREADS 0
#endif

extern void *py_tls_current_exc_storage;

static _Thread_local void *g_tls_pcc_py_gc_minor_current = (void *)0;
static _Thread_local void *g_tls_pcc_py_gc_pending_minor_block = (void *)0;
#if PCC_WITH_THREADS
static _Thread_local int32_t g_tls_pcc_py_gc_minor_graph_lock_depth = 0;
static int32_t g_pcc_py_gc_minor_graph_lock = 0;
#endif

void *pcc_py_gc_minor_current_get(void) {
    return g_tls_pcc_py_gc_minor_current;
}

void pcc_py_gc_minor_current_set(void *block) {
    g_tls_pcc_py_gc_minor_current = block;
}

void *pcc_py_gc_pending_minor_block_get(void) {
    return g_tls_pcc_py_gc_pending_minor_block;
}

void pcc_py_gc_pending_minor_block_set(void *block) {
    g_tls_pcc_py_gc_pending_minor_block = block;
}

void pcc_py_gc_minor_graph_lock(void) {
#if !PCC_WITH_THREADS
    return;
#else
    int32_t expected = 0;
    if (g_tls_pcc_py_gc_minor_graph_lock_depth > 0) {
        g_tls_pcc_py_gc_minor_graph_lock_depth += 1;
        return;
    }
    while (!__atomic_compare_exchange_n(
        &g_pcc_py_gc_minor_graph_lock,
        &expected,
        1,
        0,
        __ATOMIC_ACQ_REL,
        __ATOMIC_ACQUIRE
    )) {
        expected = 0;
        pcc_thread_safepoint();
    }
    g_tls_pcc_py_gc_minor_graph_lock_depth = 1;
#endif
}

void pcc_py_gc_minor_graph_unlock(void) {
#if !PCC_WITH_THREADS
    return;
#else
    if (g_tls_pcc_py_gc_minor_graph_lock_depth <= 0) {
        return;
    }
    g_tls_pcc_py_gc_minor_graph_lock_depth -= 1;
    if (g_tls_pcc_py_gc_minor_graph_lock_depth > 0) {
        return;
    }
    __atomic_store_n(
        &g_pcc_py_gc_minor_graph_lock, 0, __ATOMIC_RELEASE
    );
#endif
}
