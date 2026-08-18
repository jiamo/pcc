#include "py_internal.h"

/* Value-position `enumerate(iterable[, start])` support for the strict
 * no-libpython subset. The for-loop form is desugared by the frontend
 * (for_normalization_lowering); this helper covers enumerate used as a
 * VALUE (list(enumerate(xs)), passed to calls, ...) by EAGERLY
 * materialising a list of (index, item) tuples — the same eager
 * convention the subset already uses for generator yield-lists.
 *
 * This file is retained as the C oracle.  Production ownership lives in
 * py_iter.py.  Both implementations raise TypeError (via py_obj_iter) for
 * non-iterables and propagate pending exceptions from iteration. */

static PyObject *enumerate_require_result(
    PyObject *result,
    const char *helper_name,
    const char *message
) {
    if (result == NULL) {
        py_runtime_error_if_unset(helper_name, message);
    }
    return result;
}

static int enumerate_prepare_root(PyObject **slot, void **out_handle) {
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

static PyObject *enumerate_reload_root(PyObject **slot, void *handle) {
    if (slot == NULL) return NULL;
    if (handle != NULL) *slot = pcc_gc_load_ptr(NULL, slot);
    return *slot;
}

static void enumerate_finish_root(void *handle) {
    if (handle != NULL) pcc_gc_scheduler_root_unregister_handle(handle);
}

PyObject *py_enumerate_list(PyObject *iterable, int64_t start) {
    PyObject *it_storage = py_obj_iter(iterable);
    if (it_storage == NULL) return NULL;
    void *it_handle = NULL;
    if (enumerate_prepare_root(&it_storage, &it_handle) != 0) {
        py_decref(it_storage);
        return NULL;
    }
    PyObject *out = py_list_new(4);
    if (out == NULL) {
        enumerate_require_result(
            NULL,
            "py_list_new",
            "enumerate could not allocate its result list"
        );
        enumerate_finish_root(it_handle);
        py_decref(it_storage);
        return NULL;
    }
    pcc_gc_pin(out);
    int64_t index = start;
    for (;;) {
        PyObject *item_storage = py_obj_next(
            enumerate_reload_root(&it_storage, it_handle)
        );
        enumerate_reload_root(&it_storage, it_handle);
        if (item_storage == NULL) {
            if (py_err_occurred()) {
                /* Clean exhaustion is signalled by StopIteration
                 * (py_dict_fromkeys pattern); anything else
                 * propagates. */
                PyObject *cur = py_current_exception();
                PyObject *stop = py_exc_builtin_class(PY_EXC_STOPITERATION);
                if (py_exc_matches(cur, stop)) {
                    py_clear_exception();
                } else {
                    pcc_gc_unpin(out);
                    enumerate_finish_root(it_handle);
                    py_decref(it_storage);
                    py_decref(out);
                    return NULL;
                }
            }
            break;
        }
        void *item_handle = NULL;
        if (enumerate_prepare_root(&item_storage, &item_handle) != 0) {
            py_decref(item_storage);
            pcc_gc_unpin(out);
            enumerate_finish_root(it_handle);
            py_decref(it_storage);
            py_decref(out);
            return NULL;
        }
        PyObject *tup = py_tuple_new(2);
        if (tup == NULL) {
            enumerate_require_result(
                NULL,
                "py_tuple_new",
                "enumerate could not allocate an output pair"
            );
            PyObject *item = enumerate_reload_root(
                &item_storage, item_handle
            );
            enumerate_finish_root(item_handle);
            py_decref(item);
            pcc_gc_unpin(out);
            enumerate_finish_root(it_handle);
            py_decref(it_storage);
            py_decref(out);
            return NULL;
        }
        pcc_gc_pin(tup);
        /* py_tuple_set_item BORROWS (balanced store): drop our own refs
         * once the tuple holds its own. */
        PyObject *idx_obj = py_int_from_i64(index);
        if (idx_obj == NULL) {
            enumerate_require_result(
                NULL,
                "py_int_from_i64",
                "enumerate could not allocate an index object"
            );
            PyObject *item = enumerate_reload_root(
                &item_storage, item_handle
            );
            enumerate_finish_root(item_handle);
            py_decref(item);
            pcc_gc_unpin(tup);
            py_decref(tup);
            pcc_gc_unpin(out);
            enumerate_finish_root(it_handle);
            py_decref(it_storage);
            py_decref(out);
            return NULL;
        }
        void *idx_handle = NULL;
        if (enumerate_prepare_root(&idx_obj, &idx_handle) != 0) {
            py_decref(idx_obj);
            PyObject *item = enumerate_reload_root(
                &item_storage, item_handle
            );
            enumerate_finish_root(item_handle);
            py_decref(item);
            pcc_gc_unpin(tup);
            py_decref(tup);
            pcc_gc_unpin(out);
            enumerate_finish_root(it_handle);
            py_decref(it_storage);
            py_decref(out);
            return NULL;
        }
        py_tuple_set_item(tup, 0, idx_obj);
        idx_obj = enumerate_reload_root(&idx_obj, idx_handle);
        enumerate_finish_root(idx_handle);
        py_decref(idx_obj);
        PyObject *item = enumerate_reload_root(&item_storage, item_handle);
        py_tuple_set_item(tup, 1, item);
        item = enumerate_reload_root(&item_storage, item_handle);
        enumerate_finish_root(item_handle);
        py_decref(item);
        py_list_append(out, tup);
        if (py_err_occurred()) {
            pcc_gc_unpin(tup);
            py_decref(tup);
            pcc_gc_unpin(out);
            enumerate_finish_root(it_handle);
            py_decref(it_storage);
            py_decref(out);
            return NULL;
        }
        pcc_gc_unpin(tup);
        py_decref(tup);
        index++;
    }
    enumerate_finish_root(it_handle);
    py_decref(it_storage);
    pcc_gc_unpin(out);
    return out;
}
