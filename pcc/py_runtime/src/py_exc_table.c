/* pcc/py_runtime/src/py_exc_table.c
 *
 * Built-in exception class lazy-bootstrap accessor.
 *
 * The static tables (PY_EXC_BUILTIN_NAMES, PY_EXC_PARENT) and the
 * g_exc_classes cache all live in py_substrate.c now — that's the
 * always-C bottom of the runtime, safe from being swapped out by
 * Phase 4c pcc-Python ports. This file only contains the lookup
 * LOGIC (which IS ported to py_exc_table.py in pcc-py builds).
 *
 * Contains:
 *   py_exc_builtin_class   (public)   — lazy allocation of a builtin
 *                                       exception class
 */
#include "py_internal.h"


PyClassObject *py_exc_builtin_class(int64_t tag) {
    if (tag < 0 || tag >= PY_EXC_N_BUILTIN) {
        tag = PY_EXC_EXCEPTION;
    }
    PyClassObject *cached = (PyClassObject *)py_subs_exc_cache_get((int32_t)tag);
    if (cached != NULL) {
        return cached;
    }
    int32_t parent = py_subs_exc_parent((int32_t)tag);
    PyClassObject *base = NULL;
    if (parent >= 0) {
        base = py_exc_builtin_class((int64_t)parent);
    }
    PyClassObject *bases_arr[1];
    int32_t n_bases = 0;
    if (base != NULL) {
        bases_arr[0] = base;
        n_bases = 1;
    }
    const char *name = py_subs_exc_name((int32_t)tag);
    PyClassObject *cls = py_class_new(
        name,
        n_bases ? bases_arr : NULL, n_bases,
        /*field_names=*/NULL, /*n_fields=*/0
    );
    if (cls != NULL) {
        cls->h.flags |= PY_FLAG_IMMORTAL;
        py_subs_exc_cache_set((int32_t)tag, cls);
    }
    return cls;
}
