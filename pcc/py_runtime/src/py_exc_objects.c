/* pcc/py_runtime/src/py_exc_objects.c
 *
 * Exception-object construction / accessors / deallocation.
 * Split out of py_exc.c so pcc-Python ports can replace this
 * subset independently.
 *
 * Contains:
 *   py_exc_alloc            (public)
 *   py_exc_new              (public) — by builtin tag
 *   py_exc_new_with_class   (public) — by PyClassObject*
 *   py_exc_set_cause        (public)
 *   py_exc_set_context      (public)
 *   py_exc_get_message      (public)
 *   py_dealloc_exc          (public)
 *
 * Depends on py_exc_builtin_class (py_exc_table.c) for default-class
 * lookup when callers pass NULL / tag-only.
 */
#include "py_internal.h"
#include <stdlib.h>
#include <string.h>


PyExceptionObject *py_exc_alloc(PyClassObject *cls, const char *msg) {
    PyExceptionObject *e = (PyExceptionObject *)calloc(
        1, sizeof(PyExceptionObject));
    if (e == NULL) return NULL;
    e->h.refcount = 1;
    e->h.type_tag = PY_TYPE_EXC;
    e->h.flags    = 0;
    if (cls == NULL) {
        cls = py_exc_builtin_class(PY_EXC_EXCEPTION);
    }
    py_incref((PyObject *)cls);
    e->exc_class = cls;
    if (msg != NULL) {
        PyObject *s = py_str_new(msg, (int64_t)strlen(msg));
        e->message = s;   /* owned ref from py_str_new */
    } else {
        py_incref(py_None);
        e->message = py_None;
    }
    e->cause      = NULL;
    e->context    = NULL;
    e->traceback  = NULL;
    e->n_frames   = 0;
    e->cap_frames = 0;
    return e;
}


PyObject *py_exc_new(int64_t type_tag, const char *msg) {
    PyClassObject *cls = py_exc_builtin_class(type_tag);
    PyExceptionObject *e = py_exc_alloc(cls, msg);
    return (PyObject *)e;
}


PyObject *py_exc_new_with_class(PyObject *cls, const char *msg) {
    if (cls == NULL || py_type_of(cls) != PY_TYPE_CLASS) {
        return py_exc_new(PY_EXC_EXCEPTION, msg);
    }
    PyExceptionObject *e = py_exc_alloc((PyClassObject *)cls, msg);
    return (PyObject *)e;
}


void py_exc_set_cause(PyObject *exc, PyObject *cause) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) return;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    PyObject *old = e->cause;
    if (cause != NULL) py_incref(cause);
    e->cause = cause;
    if (old != NULL) py_decref(old);
}


void py_exc_set_context(PyObject *exc, PyObject *context) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) return;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    PyObject *old = e->context;
    if (context != NULL) py_incref(context);
    e->context = context;
    if (old != NULL) py_decref(old);
}


/* str(exc) — return the exception's message PyObject (borrowed). */
PyObject *py_exc_get_message(PyObject *exc) {
    if (exc == NULL) return NULL;
    if (py_type_of(exc) != PY_TYPE_EXC) return NULL;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    return e->message;  /* borrowed */
}


void py_dealloc_exc(PyObject *o) {
    PyExceptionObject *e = (PyExceptionObject *)o;
    if (e->exc_class) py_decref((PyObject *)e->exc_class);
    if (e->message)   py_decref(e->message);
    if (e->cause)     py_decref(e->cause);
    if (e->context)   py_decref(e->context);
    if (e->traceback) free(e->traceback);
    free(e);
}
