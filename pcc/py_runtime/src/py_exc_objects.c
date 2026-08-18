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
    PyExceptionObject *e = (PyExceptionObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyExceptionObject), PY_TYPE_EXC, 0);
    if (e == NULL) return NULL;
    memset((char *)e + sizeof(PyObjectHeader), 0,
           sizeof(PyExceptionObject) - sizeof(PyObjectHeader));
    if (cls == NULL) {
        cls = py_exc_builtin_class(PY_EXC_EXCEPTION);
    }
    e->exc_class = NULL;
    pcc_gc_store_ptr((PyObject *)e, (PyObject **)&e->exc_class, (PyObject *)cls);
    if (msg != NULL) {
        PyObject *s = py_str_new(msg, (int64_t)strlen(msg));
        pcc_gc_store_ptr((PyObject *)e, &e->message, s);
        if (s != NULL) py_decref(s);
    } else {
        pcc_gc_store_ptr((PyObject *)e, &e->message, py_None);
    }
    e->cause      = NULL;
    e->context    = NULL;
    e->traceback  = NULL;
    e->n_frames   = 0;
    e->cap_frames = 0;
    pcc_runtime_log_event_code(
        6, 1,
        cls != NULL ? cls->type_tag_alloc : -1, msg != NULL ? 1 : 0, e
    );
    pcc_gc_publish_initialized((PyObject *)e);
    return e;
}


PyObject *py_exc_new(int64_t type_tag, const char *msg) {
    PyClassObject *cls = py_exc_builtin_class(type_tag);
    PyExceptionObject *e = py_exc_alloc(cls, msg);
    PyObject *out = (PyObject *)e;
    pcc_runtime_log_event_code(6, 2, type_tag, msg != NULL ? 1 : 0, out);
    return out;
}


PyObject *py_exc_new_with_value(int64_t type_tag, PyObject *value) {
    PyClassObject *cls = py_exc_builtin_class(type_tag);
    PyExceptionObject *e = py_exc_alloc(cls, NULL);
    if (e == NULL) return NULL;
    if (value == NULL) value = py_None;
    pcc_gc_store_ptr((PyObject *)e, &e->message, value);
    pcc_runtime_log_event_code(6, 8, type_tag, 0, e);
    return (PyObject *)e;
}


PyObject *py_exc_new_with_class(PyObject *cls, const char *msg) {
    if (cls == NULL || py_type_of(cls) != PY_TYPE_CLASS) {
        return py_exc_new(PY_EXC_EXCEPTION, msg);
    }
    PyExceptionObject *e = py_exc_alloc((PyClassObject *)cls, msg);
    PyObject *out = (PyObject *)e;
    pcc_runtime_log_event_code(6, 9, ((PyClassObject *)cls)->type_tag_alloc, msg != NULL ? 1 : 0, out);
    return out;
}


void py_exc_set_cause(PyObject *exc, PyObject *cause) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) return;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    pcc_gc_store_ptr(exc, &e->cause, cause);
    pcc_runtime_log_event_code(6, 5, cause != NULL ? 1 : 0, 0, exc);
}


void py_exc_set_context(PyObject *exc, PyObject *context) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) return;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    pcc_gc_store_ptr(exc, &e->context, context);
    pcc_runtime_log_event_code(6, 6, context != NULL ? 1 : 0, 0, exc);
}


/* str(exc) — return the exception's message PyObject (borrowed). */
PyObject *py_exc_get_message(PyObject *exc) {
    if (exc == NULL) return NULL;
    if (py_type_of(exc) != PY_TYPE_EXC) return NULL;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    return pcc_gc_load_ptr(exc, &e->message);  /* borrowed */
}

PyObject *py_exc_get_cause(PyObject *exc) {
    if (exc == NULL) return NULL;
    if (py_type_of(exc) != PY_TYPE_EXC) return NULL;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    PyObject *cause = pcc_gc_load_ptr(exc, &e->cause);
    if (cause == NULL) cause = py_None;
    py_incref(cause);
    return cause;
}

PyObject *py_exc_get_context(PyObject *exc) {
    if (exc == NULL) return NULL;
    if (py_type_of(exc) != PY_TYPE_EXC) return NULL;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    PyObject *context = pcc_gc_load_ptr(exc, &e->context);
    if (context == NULL) context = py_None;
    py_incref(context);
    return context;
}

int64_t py_exc_traceback_len(PyObject *exc) {
    if (exc == NULL) return 0;
    if (py_type_of(exc) != PY_TYPE_EXC) return 0;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    if (e->n_frames < 0) return 0;
    return (int64_t)e->n_frames;
}


void py_dealloc_exc(PyObject *o) {
    PyExceptionObject *e = (PyExceptionObject *)o;
    PyObject *exc_class = pcc_gc_load_ptr(o, (PyObject **)&e->exc_class);
    PyObject *message = pcc_gc_load_ptr(o, &e->message);
    PyObject *cause = pcc_gc_load_ptr(o, &e->cause);
    PyObject *context = pcc_gc_load_ptr(o, &e->context);
    pcc_runtime_log_event_code(
        6, 7,
        exc_class != NULL ? ((PyClassObject *)exc_class)->type_tag_alloc : -1,
        e->n_frames,
        o
    );
    if (exc_class) py_decref(exc_class);
    if (message)   py_decref(message);
    if (cause)     py_decref(cause);
    if (context)   py_decref(context);
    if (e->traceback) free(e->traceback);
    pcc_gc_free_object_memory(o);
}
