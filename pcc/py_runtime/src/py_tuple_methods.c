/* pcc/py_runtime/src/py_tuple_methods.c
 *
 * tuple.count(x) / tuple.index(x). Kept as C helpers linked in both runtime
 * archives (OBJ_PY_CC_HELPERS) so the frontend can route tuple methods to a
 * single implementation without the pcc-Python port reimplementing them. Both
 * compare elements with py_obj_eq (port-exported), so they work for any element
 * type.
 */
#include "py_internal.h"

static int tuple_method_prepare_root(PyObject **slot, void **out_handle) {
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

static PyObject *tuple_method_reload_root(PyObject **slot, void *handle) {
    if (slot == NULL) return NULL;
    if (handle != NULL) *slot = pcc_gc_load_ptr(NULL, slot);
    return *slot;
}

static void tuple_method_finish_root(void *handle) {
    if (handle != NULL) pcc_gc_scheduler_root_unregister_handle(handle);
}

static int64_t tuple_method_scan(
    PyObject *tuple_value,
    PyObject *query_value,
    PyObject *start,
    PyObject *stop,
    int want_first,
    int *found
) {
    if (found != NULL) *found = 0;
    if (tuple_value == NULL) return 0;
    PyObject *tuple_storage = tuple_value;
    PyObject *query_storage = query_value;
    void *tuple_handle = NULL;
    void *query_handle = NULL;
    if (tuple_method_prepare_root(&tuple_storage, &tuple_handle) != 0) return 0;
    if (tuple_method_prepare_root(&query_storage, &query_handle) != 0) {
        tuple_method_finish_root(tuple_handle);
        return 0;
    }

    tuple_value = tuple_method_reload_root(&tuple_storage, tuple_handle);
    int64_t n = py_tuple_len(tuple_value);
    int64_t lo = 0;
    int64_t hi = n;
    if (start != NULL || stop != NULL) {
        int overflow = 0;
        lo = (start != NULL) ? py_int_to_i64(start, &overflow) : 0;
        if (overflow) lo = 0;
        overflow = 0;
        hi = (stop != NULL) ? py_int_to_i64(stop, &overflow) : n;
        if (stop == NULL || overflow) hi = n;
        if (lo < 0) {
            lo += n;
            if (lo < 0) lo = 0;
        }
        if (hi < 0) {
            hi += n;
            if (hi < 0) hi = 0;
        }
        if (hi > n) hi = n;
    }

    int64_t count = 0;
    for (int64_t i = lo; i < hi; i++) {
        tuple_value = tuple_method_reload_root(
            &tuple_storage, tuple_handle
        );
        query_value = tuple_method_reload_root(
            &query_storage, query_handle
        );
        PyObject *element_storage = py_tuple_get(tuple_value, i);
        if (element_storage == NULL) break;
        void *element_handle = NULL;
        if (tuple_method_prepare_root(
                &element_storage, &element_handle
            ) != 0) {
            py_decref(element_storage);
            break;
        }
        int64_t equal = py_obj_eq(element_storage, query_value);
        tuple_method_reload_root(&tuple_storage, tuple_handle);
        tuple_method_reload_root(&query_storage, query_handle);
        PyObject *element = tuple_method_reload_root(
            &element_storage, element_handle
        );
        tuple_method_finish_root(element_handle);
        py_decref(element);
        if (py_err_occurred()) {
            tuple_method_finish_root(query_handle);
            tuple_method_finish_root(tuple_handle);
            return want_first ? -1 : 0;
        }
        if (!equal) continue;
        if (found != NULL) *found = 1;
        if (want_first) {
            tuple_method_finish_root(query_handle);
            tuple_method_finish_root(tuple_handle);
            return i;
        }
        count++;
    }
    tuple_method_finish_root(query_handle);
    tuple_method_finish_root(tuple_handle);
    return count;
}

/* Number of elements equal to ``item``. */
int64_t py_tuple_count(PyObject *t, PyObject *item) {
    return tuple_method_scan(t, item, NULL, NULL, 0, NULL);
}

/* Index of the first element equal to ``item``; raises ValueError (and returns
 * -1) when absent, matching CPython tuple.index. */
int64_t py_tuple_index(PyObject *t, PyObject *item) {
    int found = 0;
    int64_t index = tuple_method_scan(t, item, NULL, NULL, 1, &found);
    if (found) return index;
    if (py_err_occurred()) return -1;
    py_raise_owned(py_exc_new(PY_EXC_VALUEERROR, "tuple.index(x): x not in tuple"));
    return -1;
}

/* tuple.index(item, start[, stop]) — search only within [start, stop). */
int64_t py_tuple_index_range(PyObject *t, PyObject *item, PyObject *start,
                             PyObject *stop) {
    int found = 0;
    int64_t index = tuple_method_scan(t, item, start, stop, 1, &found);
    if (found) return index;
    if (py_err_occurred()) return -1;
    py_raise_owned(py_exc_new(PY_EXC_VALUEERROR, "tuple.index(x): x not in tuple"));
    return -1;
}
