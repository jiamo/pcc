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

#include "../include/py_runtime.h"

static _Thread_local void *g_tls_pcc_py_gc_minor_current = (void *)0;
static _Thread_local void *g_tls_pcc_py_gc_pending_minor_block = (void *)0;
static _Thread_local int32_t g_tls_pcc_py_gc_minor_graph_lock_depth = 0;
static int32_t g_pcc_py_gc_minor_graph_lock = 0;

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
}

void pcc_py_gc_minor_graph_unlock(void) {
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
}

int32_t pcc_py_atomic_i32_load(void *slot) {
    if (slot == NULL) return 0;
    return __atomic_load_n((int32_t *)slot, __ATOMIC_RELAXED);
}

void pcc_py_atomic_i32_store(void *slot, int32_t value) {
    if (slot == NULL) return;
    __atomic_store_n((int32_t *)slot, value, __ATOMIC_RELAXED);
}

int32_t pcc_py_atomic_i32_add_fetch(void *slot, int32_t delta) {
    if (slot == NULL) return 0;
    return __atomic_add_fetch((int32_t *)slot, delta, __ATOMIC_RELAXED);
}

int64_t pcc_py_atomic_i64_load(void *slot) {
    if (slot == NULL) return 0;
    return __atomic_load_n((int64_t *)slot, __ATOMIC_ACQUIRE);
}

void pcc_py_atomic_i64_store(void *slot, int64_t value) {
    if (slot == NULL) return;
    __atomic_store_n((int64_t *)slot, value, __ATOMIC_RELEASE);
}

int64_t pcc_py_atomic_i64_add_fetch(void *slot, int64_t delta) {
    if (slot == NULL) return 0;
    return __atomic_add_fetch((int64_t *)slot, delta, __ATOMIC_ACQ_REL);
}

int64_t pcc_py_atomic_i64_dec_if_positive(void *slot) {
    if (slot == NULL) return 0;
    int64_t live = __atomic_load_n((int64_t *)slot, __ATOMIC_ACQUIRE);
    while (live > 0) {
        if (__atomic_compare_exchange_n(
            (int64_t *)slot,
            &live,
            live - 1,
            0,
            __ATOMIC_ACQ_REL,
            __ATOMIC_ACQUIRE
        )) {
            return live - 1;
        }
    }
    return live;
}
