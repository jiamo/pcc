#include "py_internal.h"
#include <stdint.h>

static int ptr_can_have_header(void *ptr) {
    uintptr_t bits = (uintptr_t)ptr;
    if (ptr == NULL) return 0;
    if ((bits & 1u) != 0u) return 0;
    if (bits < 0x1000u) return 0;
    if ((bits & 0x7u) != 0u) return 0;
#if UINTPTR_MAX > 0xffffffffu
    if ((bits >> 48) != 0u) return 0;
#endif
    return 1;
}

static PyObject *call_unary_method(PyObject *method, PyObject *self) {
    if (method == NULL) return NULL;
    if (ptr_can_have_header(method) && !PY_IS_TAGGED_INT(method)
        && py_type_of(method) == PY_TYPE_FUNC) {
        PyObject *args = py_tuple_new(1);
        if (args == NULL) return NULL;
        py_tuple_set_item(args, 0, self);
        PyObject *out = py_func_call(method, args);
        py_decref(args);
        return out;
    }
    typedef PyObject *(*UnaryMethod)(PyObject *);
    UnaryMethod fn = (UnaryMethod)(uintptr_t)method;
    return fn(self);
}

static PyObject *call_exit_method(PyObject *method, PyObject *self,
                                  PyObject *exc_type, PyObject *exc,
                                  PyObject *tb) {
    if (method == NULL) return NULL;
    if (exc_type == NULL) exc_type = py_None;
    if (exc == NULL) exc = py_None;
    if (tb == NULL) tb = py_None;
    if (ptr_can_have_header(method) && !PY_IS_TAGGED_INT(method)
        && py_type_of(method) == PY_TYPE_FUNC) {
        /* ``method`` is a bound PyFunc whose captures already hold
         * ``self``; user-code call convention is "args excludes self".
         * Pass ``(exc_type, exc, tb)`` so the bound-method entry routes
         * to its 3-arg branch and ultimately invokes the raw
         * ``__exit__(self, exc_type, exc, tb)`` with the correct
         * argument order. */
        PyObject *args = py_tuple_new(3);
        if (args == NULL) return NULL;
        py_tuple_set_item(args, 0, exc_type);
        py_tuple_set_item(args, 1, exc);
        py_tuple_set_item(args, 2, tb);
        PyObject *out = py_func_call(method, args);
        py_decref(args);
        return out;
    }
    typedef PyObject *(*ExitMethod)(PyObject *, PyObject *, PyObject *, PyObject *);
    ExitMethod fn = (ExitMethod)(uintptr_t)method;
    return fn(self, exc_type, exc, tb);
}

PyObject *py_context_enter(PyObject *manager) {
    if (manager == NULL) return NULL;
    PyObject *method = py_obj_getattr(manager, "__enter__");
    if (method == NULL) return NULL;
    return call_unary_method(method, manager);
}

int64_t py_context_exit(PyObject *manager, PyObject *exc_type,
                        PyObject *exc, PyObject *tb) {
    if (manager == NULL) return 0;
    PyObject *method = py_obj_getattr(manager, "__exit__");
    if (method == NULL) return 0;
    PyObject *result = call_exit_method(method, manager, exc_type, exc, tb);
    if (result == NULL) return 0;
    int64_t truth = py_obj_truthy(result);
    py_decref(result);
    return truth;
}
