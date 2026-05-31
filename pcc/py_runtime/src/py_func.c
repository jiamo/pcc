/* pcc/py_runtime/src/py_func.c
 *
 * Native function values for pcc-compiled Python.
 *
 * The runtime object is intentionally small: it stores a codegen-synthesized
 * adapter plus a tuple of captured values. The adapter performs all typed ABI
 * unboxing/boxing, so this file stays independent of frontend type details.
 */

#include "py_internal.h"
#include <stdlib.h>

PyObject *py_func_new_bound(
    void *entry,
    PyObject *captures_tuple,
    const char *name,
    PyObject *self_obj
) {
    if (entry == NULL) return NULL;
    PyFuncObject *f = (PyFuncObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyFuncObject), PY_TYPE_FUNC, 0);
    if (f == NULL) return NULL;
    f->entry = (PyNativeFuncEntry)entry;
    f->name = name;
    f->self_obj = NULL;
    PyObject *captures = captures_tuple == NULL ? py_tuple_new(0) : captures_tuple;
    f->captures = NULL;
    pcc_gc_store_ptr((PyObject *)f, &f->captures, captures);
    if (self_obj != NULL) {
        pcc_gc_store_ptr((PyObject *)f, &f->self_obj, self_obj);
    }
    if (captures_tuple == NULL) {
        py_decref(captures);
    }
    py_gc_track((PyObject *)f);
    return (PyObject *)f;
}

PyObject *py_func_new_named(void *entry, PyObject *captures_tuple, const char *name) {
    return py_func_new_bound(entry, captures_tuple, name, NULL);
}

PyObject *py_func_new(void *entry, PyObject *captures_tuple) {
    return py_func_new_named(entry, captures_tuple, NULL);
}

PyObject *py_func_call(PyObject *callable, PyObject *args_tuple) {
    if (callable == NULL) return NULL;
    if (PY_IS_TAGGED_INT(callable)) return NULL;
    PyObjectHeader *h = py_header(callable);
    if (h->type_tag != PY_TYPE_FUNC) return NULL;
    PyFuncObject *f = (PyFuncObject *)callable;
    if (f->entry == NULL) return NULL;
    PyObject *args = args_tuple == NULL ? py_tuple_new(0) : args_tuple;
    PyObject *captures = pcc_gc_load_ptr(callable, &f->captures);
    PyObject *result = f->entry(captures, args);
    if (args_tuple == NULL) py_decref(args);
    return result;
}

void py_dealloc_func(PyObject *o) {
    PyFuncObject *f = (PyFuncObject *)o;
    PyObject *captures = pcc_gc_load_ptr(o, &f->captures);
    PyObject *self_obj = pcc_gc_load_ptr(o, &f->self_obj);
    py_decref(captures);
    if (self_obj != NULL) py_decref(self_obj);
    pcc_gc_free_object_memory(o);
}

/* functools.partial: a callable prepending captured `bound` args to the call
 * args, then invoking `fn` via the generic call. Positional args; kwargs later. */
static PyObject *pcc_partial_entry(PyObject *captures, PyObject *args) {
    PyObject *fn = py_tuple_get(captures, 0);
    PyObject *bound = py_tuple_get(captures, 1);
    if (fn == NULL || bound == NULL) {
        if (fn != NULL) py_decref(fn);
        if (bound != NULL) py_decref(bound);
        return NULL;
    }
    int64_t nb = py_tuple_len(bound);
    int64_t na = py_tuple_len(args);
    PyObject *full = py_tuple_new(nb + na);
    if (full == NULL) { py_decref(fn); py_decref(bound); return NULL; }
    for (int64_t i = 0; i < nb; i++) py_tuple_set_item(full, i, py_tuple_get(bound, i));
    for (int64_t i = 0; i < na; i++) py_tuple_set_item(full, nb + i, py_tuple_get(args, i));
    PyObject *out = py_obj_call(fn, full, NULL);
    py_decref(full); py_decref(fn); py_decref(bound);
    return out;
}

PyObject *py_functools_partial(PyObject *fn, PyObject *bound_args) {
    if (fn == NULL) return NULL;
    PyObject *bound = bound_args == NULL ? py_tuple_new(0) : bound_args;
    if (bound == NULL) return NULL;
    PyObject *captures = py_tuple_new(2);
    if (captures == NULL) { if (bound_args == NULL) py_decref(bound); return NULL; }
    py_incref(fn); py_tuple_set_item(captures, 0, fn);
    py_incref(bound); py_tuple_set_item(captures, 1, bound);
    PyObject *p = py_func_new_bound((void *)pcc_partial_entry, captures, "partial", NULL);
    py_decref(captures);
    if (bound_args == NULL) py_decref(bound);
    return p;
}
