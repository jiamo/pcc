/* pcc/py_runtime/src/py_exc_match.c
 *
 * Exception-class MRO matcher. Split out separately so the hot path
 * can be independently ported to pcc-Python without dragging the
 * colder `py_exc_append_frame` and `py_exc_print_unhandled` paths
 * with it (those live in py_exc_traceback.c).
 *
 * Contains:
 *   py_exc_matches   (public) — MRO-aware class match
 */
#include "py_internal.h"


/* Project either an exception instance or a class object down to a
 * PyClassObject*. Returns NULL when the input is not usable. */
static PyClassObject *exc_to_class(PyObject *o) {
    if (o == NULL) return NULL;
    if (PY_IS_TAGGED_INT(o)) return NULL;
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_CLASS) {
        return (PyClassObject *)o;
    }
    if (tag == PY_TYPE_EXC) {
        return ((PyExceptionObject *)o)->exc_class;
    }
    return NULL;
}


int64_t py_exc_matches(PyObject *exc, PyObject *type) {
    PyClassObject *ecls = exc_to_class(exc);
    PyClassObject *tcls = exc_to_class(type);
    if (ecls == NULL || tcls == NULL) return 0;
    if (ecls->mro == NULL) {
        return ecls == tcls;
    }
    for (int32_t i = 0; i < ecls->n_mro; i++) {
        if (ecls->mro[i] == tcls) return 1;
    }
    return 0;
}
