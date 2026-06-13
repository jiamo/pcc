/* pcc/py_runtime/src/py_tuple_methods.c
 *
 * tuple.count(x) / tuple.index(x). Kept as C helpers linked in both runtime
 * archives (OBJ_PY_CC_HELPERS) so the frontend can route tuple methods to a
 * single implementation without the pcc-Python port reimplementing them. Both
 * compare elements with py_obj_eq (port-exported), so they work for any element
 * type.
 */
#include "py_internal.h"

/* Number of elements equal to ``item``. */
int64_t py_tuple_count(PyObject *t, PyObject *item) {
    if (t == NULL) return 0;
    int64_t n = py_tuple_len(t);
    int64_t count = 0;
    for (int64_t i = 0; i < n; i++) {
        if (py_obj_eq(py_tuple_get(t, i), item)) count++;
    }
    return count;
}

/* Index of the first element equal to ``item``; raises ValueError (and returns
 * -1) when absent, matching CPython tuple.index. */
int64_t py_tuple_index(PyObject *t, PyObject *item) {
    if (t != NULL) {
        int64_t n = py_tuple_len(t);
        for (int64_t i = 0; i < n; i++) {
            if (py_obj_eq(py_tuple_get(t, i), item)) return i;
        }
    }
    py_raise(py_exc_new(PY_EXC_VALUEERROR, "tuple.index(x): x not in tuple"));
    return -1;
}

/* tuple.index(item, start[, stop]) — search only within [start, stop).
 *
 * ``start`` and ``stop`` are boxed Python ints (the frontend passes them as
 * PyObject to keep the ABI uniform with the item argument). ``stop == NULL``
 * means "to the end" (the 2-argument form). Both indices follow CPython's
 * slice-index clamping (Objects/tupleobject.c tupleindex): a negative value is
 * relative to the length and floored at 0, and ``stop`` is capped at the
 * length. Raises ValueError (and returns -1) when the item is not found in the
 * range, matching CPython. */
int64_t py_tuple_index_range(PyObject *t, PyObject *item, PyObject *start,
                             PyObject *stop) {
    if (t != NULL) {
        int64_t n = py_tuple_len(t);
        int overflow = 0;
        int64_t lo = (start != NULL) ? py_int_to_i64(start, &overflow) : 0;
        if (overflow) lo = 0;
        overflow = 0;
        int64_t hi = (stop != NULL) ? py_int_to_i64(stop, &overflow) : n;
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
        for (int64_t i = lo; i < hi; i++) {
            if (py_obj_eq(py_tuple_get(t, i), item)) return i;
        }
    }
    py_raise(py_exc_new(PY_EXC_VALUEERROR, "tuple.index(x): x not in tuple"));
    return -1;
}
