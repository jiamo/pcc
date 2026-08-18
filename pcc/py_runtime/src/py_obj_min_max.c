/* pcc/py_runtime/src/py_obj_min_max.c
 *
 * Generic min()/max() over any iterable, comparing elements with
 * py_obj_cmp_threeway (so it works for str / bytes / tuple / mixed-numeric
 * elements, not just the int-accumulator fast path in the frontend's
 * _maybe_emit_min_max_iter). Retained as the differential C oracle; production
 * pcc-Python ownership lives in py_obj_ops_compare.py. Focused C probes link
 * this source explicitly rather than changing the production C archive.
 */
#include "py_internal.h"

static int min_max_prepare_root(PyObject **slot, void **out_handle) {
    if (out_handle == NULL) return -1;
    *out_handle = NULL;
    if (slot == NULL || *slot == NULL || PY_IS_TAGGED_INT(*slot)) return 0;
    int64_t backend = pcc_gc_backend();
    if (
        backend != PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        && backend != PCC_GC_KIND_COLORED_RELOCATING
    ) return 0;
    void *handle = pcc_gc_scheduler_root_register_handle(slot);
    if (handle == NULL) return -1;
    *slot = pcc_gc_load_ptr(NULL, slot);
    *out_handle = handle;
    return 0;
}

static PyObject *min_max_reload_root(PyObject **slot, void *handle) {
    if (slot == NULL) return NULL;
    if (handle != NULL) *slot = pcc_gc_load_ptr(NULL, slot);
    return *slot;
}

static void min_max_finish_root(void *handle) {
    if (handle != NULL) pcc_gc_scheduler_root_unregister_handle(handle);
}

/* min(iterable) / max(iterable). want_max != 0 -> max, else min. Consumes the
 * iterable via the iterator protocol and returns the extreme element (a new
 * reference, borrowed-then-incref'd from the iterator's items). On an empty
 * iterable raises ValueError (matching CPython) and returns NULL. */
PyObject *py_obj_min_max(PyObject *iterable, int64_t want_max) {
    if (iterable == NULL) return NULL;
    PyObject *it_storage = py_obj_iter(iterable);
    if (it_storage == NULL) return NULL;
    void *it_handle = NULL;
    if (min_max_prepare_root(&it_storage, &it_handle) != 0) {
        py_decref(it_storage);
        return NULL;
    }

    PyObject *root_storage[2] = {NULL, NULL};
    PyObject **best_slot = &root_storage[0];
    PyObject **element_slot = &root_storage[1];
    *best_slot = py_obj_next(min_max_reload_root(&it_storage, it_handle));
    min_max_reload_root(&it_storage, it_handle);
    if (*best_slot == NULL) {
        /* Empty: clear a terminal StopIteration, then raise ValueError. */
        if (py_err_occurred()) {
            PyObject *cur = py_current_exception();
            PyClassObject *stop = py_exc_builtin_class(PY_EXC_STOPITERATION);
            if (stop != NULL && py_exc_matches(cur, (PyObject *)stop)) {
                py_clear_exception();
            } else {
                min_max_finish_root(it_handle);
                py_decref(it_storage);
                return NULL;  /* a real error during iteration */
            }
        }
        min_max_finish_root(it_handle);
        py_decref(it_storage);
        py_raise_owned(py_exc_new(PY_EXC_VALUEERROR,
                            want_max ? "max() arg is an empty sequence"
                                     : "min() arg is an empty sequence"));
        return NULL;
    }
    void *best_handle = NULL;
    if (min_max_prepare_root(best_slot, &best_handle) != 0) {
        py_decref(*best_slot);
        min_max_finish_root(it_handle);
        py_decref(it_storage);
        return NULL;
    }

    for (;;) {
        *element_slot = py_obj_next(
            min_max_reload_root(&it_storage, it_handle)
        );
        min_max_reload_root(&it_storage, it_handle);
        min_max_reload_root(best_slot, best_handle);
        if (*element_slot == NULL) {
            if (py_err_occurred()) {
                PyObject *cur = py_current_exception();
                PyClassObject *stop = py_exc_builtin_class(PY_EXC_STOPITERATION);
                if (stop != NULL && py_exc_matches(cur, (PyObject *)stop)) {
                    py_clear_exception();
                } else {
                    PyObject *best = min_max_reload_root(
                        best_slot, best_handle
                    );
                    min_max_finish_root(best_handle);
                    py_decref(best);
                    min_max_finish_root(it_handle);
                    py_decref(it_storage);
                    return NULL;  /* propagate a real error */
                }
            }
            break;
        }
        void *element_handle = NULL;
        if (min_max_prepare_root(element_slot, &element_handle) != 0) {
            py_decref(*element_slot);
            PyObject *best = min_max_reload_root(best_slot, best_handle);
            min_max_finish_root(best_handle);
            py_decref(best);
            min_max_finish_root(it_handle);
            py_decref(it_storage);
            return NULL;
        }
        /* Compare via py_obj_lt (exported by the pcc-Python port;
         * py_obj_cmp_threeway is internal there). max: replace when
         * best < el; min: replace when el < best. */
        int replace = want_max
            ? (py_obj_lt(*best_slot, *element_slot) != 0)
            : (py_obj_lt(*element_slot, *best_slot) != 0);
        min_max_reload_root(best_slot, best_handle);
        min_max_reload_root(element_slot, element_handle);
        if (replace) {
            PyObject **old_best_slot = best_slot;
            best_slot = element_slot;
            element_slot = old_best_slot;
            void *old_best_handle = best_handle;
            best_handle = element_handle;
            element_handle = old_best_handle;
        }
        PyObject *discard = *element_slot;
        *element_slot = NULL;
        min_max_finish_root(element_handle);
        py_decref(discard);
    }

    min_max_finish_root(it_handle);
    py_decref(it_storage);
    PyObject *best = min_max_reload_root(best_slot, best_handle);
    min_max_finish_root(best_handle);
    return best;
}
