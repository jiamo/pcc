/* pcc/py_runtime/src/py_dunder.c
 *
 * Small dynamic dunder helpers that runtime-high pcc-Python modules can
 * call through extern(). These stay in C for now because pcc-Python cannot
 * yet call arbitrary function pointers loaded from class method tables.
 */

#include "py_internal.h"

#include <stdlib.h>
#include <string.h>

PyObject *py_int_to_str_obj(PyObject *o) {
    if (o == NULL || py_type_of(o) != PY_TYPE_INT) return NULL;

    PyIntObject *b = py_bigint_from_any(o);
    if (b == NULL) return NULL;

    char *raw = py_bigint_to_cstr(b);
    free(b);
    if (raw == NULL) return NULL;

    PyObject *s = py_str_new(raw, (int64_t)strlen(raw));
    free(raw);
    return s;
}

PyObject *py_user_str_dispatch(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return NULL;
    int32_t tag = py_header(o)->type_tag;
    if (tag != PY_TYPE_INSTANCE && tag < PY_TYPE_USER) return NULL;

    PyInstanceObject *inst = (PyInstanceObject *)o;
    if (inst->cls == NULL) return NULL;

    PyObject *func = py_class_lookup(inst->cls, "__str__");
    if (func == NULL) return NULL;

    typedef PyObject *(*UnaryMethod)(PyObject *);
    UnaryMethod meth = (UnaryMethod)(uintptr_t)func;
    return meth(o);
}
