#include "py_internal.h"

/* Value-position `enumerate(iterable[, start])` support for the strict
 * no-libpython subset. The for-loop form is desugared by the frontend
 * (for_normalization_lowering); this helper covers enumerate used as a
 * VALUE (list(enumerate(xs)), passed to calls, ...) by EAGERLY
 * materialising a list of (index, item) tuples — the same eager
 * convention the subset already uses for generator yield-lists.
 *
 * C-only helper (no pcc-Python mirror): linked into both runtime
 * archives via OBJ_PY_CC_HELPERS. Raises TypeError (via py_obj_iter)
 * for non-iterables and propagates pending exceptions from iteration. */

PyObject *py_enumerate_list(PyObject *iterable, int64_t start) {
    PyObject *it = py_obj_iter(iterable);
    if (it == NULL) return NULL;
    PyObject *out = py_list_new(4);
    if (out == NULL) {
        py_decref(it);
        return NULL;
    }
    int64_t index = start;
    for (;;) {
        PyObject *item = py_obj_next(it);
        if (item == NULL) {
            if (py_err_occurred()) {
                /* Clean exhaustion is signalled by StopIteration
                 * (py_dict_fromkeys pattern); anything else
                 * propagates. */
                PyObject *cur = py_current_exception();
                PyObject *stop = py_exc_builtin_class(PY_EXC_STOPITERATION);
                if (py_exc_matches(cur, stop)) {
                    py_clear_exception();
                } else {
                    py_decref(it);
                    py_decref(out);
                    return NULL;
                }
            }
            break;
        }
        PyObject *tup = py_tuple_new(2);
        if (tup == NULL) {
            py_decref(item);
            py_decref(it);
            py_decref(out);
            return NULL;
        }
        /* py_tuple_set_item BORROWS (balanced store): drop our own refs
         * once the tuple holds its own. */
        PyObject *idx_obj = py_int_from_i64(index);
        py_tuple_set_item(tup, 0, idx_obj);
        py_decref(idx_obj);
        py_tuple_set_item(tup, 1, item);
        py_decref(item);
        py_list_append(out, tup);
        py_decref(tup);
        index++;
    }
    py_decref(it);
    return out;
}
