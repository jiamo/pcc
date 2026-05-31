/* User protocol dunder dispatch.
 *
 * This file centralizes dynamic data-model lookups for protocols that are
 * implemented by generic object operations:
 *
 *   __len__ / __bool__ / __contains__
 *   __getitem__ / __setitem__ / __delitem__
 *
 * The class method table currently supports both raw C function pointers and
 * PY_TYPE_FUNC wrappers.  The helpers below support both representations.
 */

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

static int is_user_instance(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    int32_t tag = py_type_of(o);
    return tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER;
}

static PyObject *lookup_dunder(PyObject *o, const char *name) {
    if (!is_user_instance(o)) return NULL;
    PyInstanceObject *inst = (PyInstanceObject *)o;
    if (inst->cls == NULL) return NULL;
    return py_class_lookup(inst->cls, name);
}

static PyObject *call_unary(PyObject *method, PyObject *self) {
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
    typedef PyObject *(*Unary)(PyObject *);
    return ((Unary)(uintptr_t)method)(self);
}

static PyObject *call_binary(PyObject *method, PyObject *self, PyObject *arg) {
    if (method == NULL) return NULL;
    if (ptr_can_have_header(method) && !PY_IS_TAGGED_INT(method)
        && py_type_of(method) == PY_TYPE_FUNC) {
        PyObject *args = py_tuple_new(2);
        if (args == NULL) return NULL;
        py_tuple_set_item(args, 0, self);
        py_tuple_set_item(args, 1, arg);
        PyObject *out = py_func_call(method, args);
        py_decref(args);
        return out;
    }
    typedef PyObject *(*Binary)(PyObject *, PyObject *);
    return ((Binary)(uintptr_t)method)(self, arg);
}

static PyObject *call_ternary(PyObject *method, PyObject *self,
                              PyObject *a, PyObject *b) {
    if (method == NULL) return NULL;
    if (ptr_can_have_header(method) && !PY_IS_TAGGED_INT(method)
        && py_type_of(method) == PY_TYPE_FUNC) {
        PyObject *args = py_tuple_new(3);
        if (args == NULL) return NULL;
        py_tuple_set_item(args, 0, self);
        py_tuple_set_item(args, 1, a);
        py_tuple_set_item(args, 2, b);
        PyObject *out = py_func_call(method, args);
        py_decref(args);
        return out;
    }
    typedef PyObject *(*Ternary)(PyObject *, PyObject *, PyObject *);
    return ((Ternary)(uintptr_t)method)(self, a, b);
}

int64_t py_user_len_dispatch(PyObject *o, int64_t *handled) {
    if (handled) *handled = 0;
    PyObject *method = lookup_dunder(o, "__len__");
    if (method == NULL) return 0;
    if (handled) *handled = 1;
    PyObject *result = call_unary(method, o);
    if (result == NULL) return 0;
    int overflow = 0;
    int64_t value = py_int_to_i64(result, &overflow);
    py_decref(result);
    if (overflow || value < 0) return 0;
    return value;
}

int64_t py_user_bool_dispatch(PyObject *o, int64_t *handled) {
    if (handled) *handled = 0;
    PyObject *method = lookup_dunder(o, "__bool__");
    if (method == NULL) return 0;
    if (handled) *handled = 1;
    PyObject *result = call_unary(method, o);
    if (result == NULL) return 0;
    int64_t truth = py_obj_truthy(result);
    py_decref(result);
    return truth ? 1 : 0;
}

int64_t py_obj_index_i64(PyObject *o) {
    if (o == NULL) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "object cannot be interpreted as an integer"));
        return 0;
    }
    if (PY_IS_TAGGED_INT(o)) return py_int_value_i64(o);
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_INT) return py_int_value_i64(o);
    if (tag == PY_TYPE_BOOL) return o == py_True ? 1 : 0;
    if (!is_user_instance(o)) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "object cannot be interpreted as an integer"));
        return 0;
    }
    PyObject *method = lookup_dunder(o, "__index__");
    if (method == NULL) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "object cannot be interpreted as an integer"));
        return 0;
    }
    PyObject *result = call_unary(method, o);
    if (result == NULL) return 0;
    if (PY_IS_TAGGED_INT(result) || py_type_of(result) == PY_TYPE_INT) {
        int overflow = 0;
        int64_t value = py_int_to_i64(result, &overflow);
        py_decref(result);
        if (!overflow) return value;
    } else {
        py_decref(result);
    }
    py_raise(py_exc_new(PY_EXC_TYPEERROR, "__index__ returned non-int"));
    return 0;
}

int64_t py_user_contains_dispatch(PyObject *o, PyObject *item,
                                  int64_t *handled) {
    if (handled) *handled = 0;
    PyObject *method = lookup_dunder(o, "__contains__");
    if (method == NULL) return 0;
    if (handled) *handled = 1;
    PyObject *result = call_binary(method, o, item);
    if (result == NULL) return 0;
    int64_t truth = py_obj_truthy(result);
    py_decref(result);
    return truth ? 1 : 0;
}

/* Dispatch a user __eq__ for py_obj_eq (used by dict/set key lookup, the ``==``
 * runtime path, etc.). Returns a TRI-STATE: -1 = no __eq__ defined (caller
 * falls back to identity), 0 = __eq__ said not-equal, 1 = __eq__ said equal.
 * Uses lookup_dunder (unbound func) + call_binary, avoiding the bound-method
 * double-self bug. A NotImplemented result also yields -1 (fall back). */
int64_t py_user_eq_dispatch(PyObject *a, PyObject *b) {
    /* Recursion guard: a user __eq__ that compares fields which route back
     * through py_obj_eq -> here (nested / self-referential structures) could
     * recurse to a stack overflow. Bail to identity (-1) past a depth well
     * above realistic nesting but far below the C stack limit. Thread-local so
     * concurrent comparisons don't clobber the counter. This is the
     * self-host-safety guard for the py_obj_eq instance dispatch. */
    static __thread int _eq_depth = 0;
    PyObject *method = lookup_dunder(a, "__eq__");
    if (method == NULL) return -1;
    if (_eq_depth >= 64) return -1;
    _eq_depth++;
    PyObject *result = call_binary(method, a, b);
    _eq_depth--;
    if (result == NULL) return 0;        /* __eq__ raised: error already set */
    if (result == py_NotImplemented) {
        py_decref(result);
        return -1;
    }
    int64_t truth = py_obj_truthy(result);
    py_decref(result);
    return truth ? 1 : 0;
}

PyObject *py_user_getitem_dispatch(PyObject *o, PyObject *key) {
    PyObject *method = lookup_dunder(o, "__getitem__");
    if (method == NULL) return NULL;
    return call_binary(method, o, key);
}

PyObject *py_user_matmul_dispatch(PyObject *a, PyObject *b) {
    PyObject *method = lookup_dunder(a, "__matmul__");
    if (method != NULL) {
        PyObject *result = call_binary(method, a, b);
        if (result != py_NotImplemented) return result;
        py_decref(result);
    }
    method = lookup_dunder(b, "__rmatmul__");
    if (method != NULL) {
        PyObject *result = call_binary(method, b, a);
        if (result != py_NotImplemented) return result;
        py_decref(result);
    }
    py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for @"));
    return NULL;
}

int64_t py_user_setitem_dispatch(PyObject *o, PyObject *key, PyObject *value,
                                 int64_t *handled) {
    if (handled) *handled = 0;
    PyObject *method = lookup_dunder(o, "__setitem__");
    if (method == NULL) return -1;
    if (handled) *handled = 1;
    PyObject *result = call_ternary(method, o, key, value);
    if (result == NULL) return -1;
    py_decref(result);
    return 0;
}

int64_t py_user_delitem_dispatch(PyObject *o, PyObject *key,
                                 int64_t *handled) {
    if (handled) *handled = 0;
    PyObject *method = lookup_dunder(o, "__delitem__");
    if (method == NULL) return -1;
    if (handled) *handled = 1;
    PyObject *result = call_binary(method, o, key);
    if (result == NULL) return -1;
    py_decref(result);
    return 0;
}
