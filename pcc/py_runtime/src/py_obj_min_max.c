/* pcc/py_runtime/src/py_obj_min_max.c
 *
 * Generic min()/max() over any iterable, comparing elements with
 * py_obj_cmp_threeway (so it works for str / bytes / tuple / mixed-numeric
 * elements, not just the int-accumulator fast path in the frontend's
 * _maybe_emit_min_max_iter). Kept as a C helper linked in both runtime
 * archives (OBJ_PY_CC_HELPERS) — see py_int_modexp.c for the same pattern —
 * so the frontend can route non-int-element min()/max() to one implementation
 * without a pcc-Python port reimplementation.
 */
#include "py_internal.h"

/* min(iterable) / max(iterable). want_max != 0 -> max, else min. Consumes the
 * iterable via the iterator protocol and returns the extreme element (a new
 * reference, borrowed-then-incref'd from the iterator's items). On an empty
 * iterable raises ValueError (matching CPython) and returns NULL. */
PyObject *py_obj_min_max(PyObject *iterable, int64_t want_max) {
    if (iterable == NULL) return NULL;
    PyObject *it = py_obj_iter(iterable);
    if (it == NULL) return NULL;

    PyObject *best = py_obj_next(it);
    if (best == NULL) {
        /* Empty: clear a terminal StopIteration, then raise ValueError. */
        if (py_err_occurred()) {
            PyObject *cur = py_current_exception();
            PyClassObject *stop = py_exc_builtin_class(PY_EXC_STOPITERATION);
            if (stop != NULL && py_exc_matches(cur, (PyObject *)stop)) {
                py_clear_exception();
            } else {
                py_decref(it);
                return NULL;  /* a real error during iteration */
            }
        }
        py_decref(it);
        py_raise(py_exc_new(PY_EXC_VALUEERROR,
                            want_max ? "max() arg is an empty sequence"
                                     : "min() arg is an empty sequence"));
        return NULL;
    }

    for (;;) {
        PyObject *el = py_obj_next(it);
        if (el == NULL) {
            if (py_err_occurred()) {
                PyObject *cur = py_current_exception();
                PyClassObject *stop = py_exc_builtin_class(PY_EXC_STOPITERATION);
                if (stop != NULL && py_exc_matches(cur, (PyObject *)stop)) {
                    py_clear_exception();
                } else {
                    py_decref(best);
                    py_decref(it);
                    return NULL;  /* propagate a real error */
                }
            }
            break;
        }
        /* Compare via py_obj_lt (exported by the pcc-Python port;
         * py_obj_cmp_threeway is internal there). max: replace when
         * best < el; min: replace when el < best. */
        int replace = want_max
            ? (py_obj_lt(best, el) != 0)
            : (py_obj_lt(el, best) != 0);
        if (replace) {
            py_decref(best);
            best = el;  /* keep el */
        } else {
            py_decref(el);
        }
    }

    py_decref(it);
    return best;
}
