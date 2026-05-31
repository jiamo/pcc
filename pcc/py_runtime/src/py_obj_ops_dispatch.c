/* pcc/py_runtime/src/py_obj_ops_dispatch.c
 *
 * Type-tag dispatch for the simpler generic ops: truthy / len /
 * subscript (getitem / setitem / delitem / slice) / attribute
 * (getattr / setattr) / call / isinstance.
 *
 * Split out of py_obj_ops.c so this half can be replaced by
 * py_obj_ops_dispatch.py while the compare/hash/sorted half
 * (py_obj_ops_compare.c) stays C.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>

static int dispatch_ptr_can_have_header(void *ptr) {
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

static int dispatch_is_heap_obj(PyObject *o) {
    return o != NULL && !PY_IS_TAGGED_INT(o) && dispatch_ptr_can_have_header(o);
}

static PyObject *dispatch_call_method_with_args(
    PyObject *method,
    PyObject *self,
    PyObject *args
) {
    if (method == NULL) return NULL;
    int64_t n = args == NULL ? 0 : py_tuple_len(args);
    if (
        dispatch_is_heap_obj(method)
        && py_type_of(method) == PY_TYPE_FUNC
    ) {
        PyObject *full_args = py_tuple_new(n + 1);
        if (full_args == NULL) return NULL;
        py_tuple_set_item(full_args, 0, self);
        for (int64_t i = 0; i < n; i++) {
            PyObject *item = py_tuple_get(args, i);
            py_tuple_set_item(full_args, i + 1, item);
            py_decref(item);
        }
        PyObject *out = py_func_call(method, full_args);
        py_decref(full_args);
        return out;
    }
    if (n == 0) {
        typedef PyObject *(*M0)(PyObject *);
        return ((M0)(uintptr_t)method)(self);
    }
    if (n == 1) {
        PyObject *a0 = py_tuple_get(args, 0);
        typedef PyObject *(*M1)(PyObject *, PyObject *);
        PyObject *out = ((M1)(uintptr_t)method)(self, a0);
        py_decref(a0);
        return out;
    }
    if (n == 2) {
        PyObject *a0 = py_tuple_get(args, 0);
        PyObject *a1 = py_tuple_get(args, 1);
        typedef PyObject *(*M2)(PyObject *, PyObject *, PyObject *);
        PyObject *out = ((M2)(uintptr_t)method)(self, a0, a1);
        py_decref(a0);
        py_decref(a1);
        return out;
    }
    if (n == 3) {
        PyObject *a0 = py_tuple_get(args, 0);
        PyObject *a1 = py_tuple_get(args, 1);
        PyObject *a2 = py_tuple_get(args, 2);
        typedef PyObject *(*M3)(PyObject *, PyObject *, PyObject *, PyObject *);
        PyObject *out = ((M3)(uintptr_t)method)(self, a0, a1, a2);
        py_decref(a0);
        py_decref(a1);
        py_decref(a2);
        return out;
    }
    py_raise(py_exc_new(PY_EXC_TYPEERROR, "too many native method args"));
    return NULL;
}

int64_t py_obj_truthy(PyObject *o) {
    if (o == NULL) return 0;
    if (o == py_None || o == py_False) return 0;
    if (o == py_True) return 1;
    if (PY_IS_TAGGED_INT(o)) return py_untag_int(o) != 0;
    int32_t tag = py_header(o)->type_tag;
    switch (tag) {
        case PY_TYPE_INT:
            return py_int_value_i64(o) != 0;
        case PY_TYPE_FLOAT: return ((PyFloatObject *)o)->value != 0.0;
        case PY_TYPE_LIST:  return ((PyListObject *)o)->length != 0;
        case PY_TYPE_TUPLE: return ((PyTupleObject *)o)->len != 0;
        case PY_TYPE_STR:   return ((PyStrObject *)o)->byte_len != 0;
        case PY_TYPE_BYTES:
        case PY_TYPE_BYTEARRAY:
        case PY_TYPE_MEMORYVIEW:
            return py_bytes_len(o) != 0;
        case PY_TYPE_DICT:  return ((PyDictObject *)o)->size != 0;
        case PY_TYPE_SET:   return ((PySetObject *)o)->size != 0;
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
                int64_t handled = 0;
                int64_t user_bool = py_user_bool_dispatch(o, &handled);
                if (handled) return user_bool ? 1 : 0;
                int64_t user_len = py_user_len_dispatch(o, &handled);
                if (handled) return user_len != 0 ? 1 : 0;
            }
            return 1;
    }
}

int64_t py_obj_type_tag(PyObject *o) {
    if (o == NULL) return -1;
    return py_type_of(o);
}

PyObject *py_obj_add(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for +"));
        return NULL;
    }
    int32_t at = py_type_of(a);
    int32_t bt = py_type_of(b);
    if (
        (at == PY_TYPE_INT || at == PY_TYPE_BOOL)
        && (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL)
    ) {
        return py_int_add(a, b);
    }
    if (at == PY_TYPE_COMPLEX || bt == PY_TYPE_COMPLEX) {
        return py_complex_add(a, b);
    }
    if (at == PY_TYPE_FLOAT || bt == PY_TYPE_FLOAT) {
        return py_float_add(a, b);
    }
    if (at == PY_TYPE_STR && bt == PY_TYPE_STR) {
        return py_str_concat(a, b);
    }
    if (
        (at == PY_TYPE_BYTES || at == PY_TYPE_BYTEARRAY)
        && (bt == PY_TYPE_BYTES || bt == PY_TYPE_BYTEARRAY)
    ) {
        return py_bytes_concat(a, b);
    }
    if (at == PY_TYPE_LIST && bt == PY_TYPE_LIST) {
        return py_list_concat(a, b);
    }
    if (at == PY_TYPE_TUPLE && bt == PY_TYPE_TUPLE) {
        return py_tuple_concat(a, b);
    }
    py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for +"));
    return NULL;
}

PyObject *py_obj_sub(PyObject *a, PyObject *b) {
    /* Generic a - b (mirrors py_obj_add). int/bool -> py_int_sub; any float ->
     * py_float_sub. Subtraction is numeric-only in Python. */
    if (a == NULL || b == NULL) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for -"));
        return NULL;
    }
    int32_t at = py_type_of(a);
    int32_t bt = py_type_of(b);
    if ((at == PY_TYPE_INT || at == PY_TYPE_BOOL)
        && (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL)) {
        return py_int_sub(a, b);
    }
    if ((at == PY_TYPE_FLOAT || at == PY_TYPE_INT || at == PY_TYPE_BOOL)
        && (bt == PY_TYPE_FLOAT || bt == PY_TYPE_INT || bt == PY_TYPE_BOOL)) {
        return py_float_sub(a, b);
    }
    py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for -"));
    return NULL;
}

PyObject *py_obj_mul(PyObject *a, PyObject *b) {
    /* Generic a * b (mirrors py_obj_add). int/bool -> py_int_mul; any-float
     * numeric -> py_float_mul; sequence * int -> repetition. */
    if (a == NULL || b == NULL) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for *"));
        return NULL;
    }
    int32_t at = py_type_of(a);
    int32_t bt = py_type_of(b);
    if ((at == PY_TYPE_INT || at == PY_TYPE_BOOL)
        && (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL)) {
        return py_int_mul(a, b);
    }
    if ((at == PY_TYPE_FLOAT || at == PY_TYPE_INT || at == PY_TYPE_BOOL)
        && (bt == PY_TYPE_FLOAT || bt == PY_TYPE_INT || bt == PY_TYPE_BOOL)) {
        return py_float_mul(a, b);
    }
    if (at == PY_TYPE_STR && (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL)) {
        return py_str_repeat(a, b);
    }
    if (bt == PY_TYPE_STR && (at == PY_TYPE_INT || at == PY_TYPE_BOOL)) {
        return py_str_repeat(b, a);
    }
    if (at == PY_TYPE_LIST && (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL)) {
        return py_list_repeat(a, py_int_value_i64(b));
    }
    if (bt == PY_TYPE_LIST && (at == PY_TYPE_INT || at == PY_TYPE_BOOL)) {
        return py_list_repeat(b, py_int_value_i64(a));
    }
    if (at == PY_TYPE_TUPLE && (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL)) {
        return py_tuple_repeat(a, py_int_value_i64(b));
    }
    if (bt == PY_TYPE_TUPLE && (at == PY_TYPE_INT || at == PY_TYPE_BOOL)) {
        return py_tuple_repeat(b, py_int_value_i64(a));
    }
    py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for *"));
    return NULL;
}

PyObject *py_obj_mod(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for %"));
        return NULL;
    }
    int32_t at = py_type_of(a);
    int32_t bt = py_type_of(b);
    if (at == PY_TYPE_STR) {
        return py_str_mod(a, b);
    }
    if (
        (at == PY_TYPE_INT || at == PY_TYPE_BOOL)
        && (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL)
    ) {
        return py_int_mod(a, b);
    }
    py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for %"));
    return NULL;
}

/* Python true division (``a / b``) for dynamically-typed operands.
 *
 * CPython's ``/`` always yields a float for numeric operands, so a DynType
 * operand (e.g. ``obj.attr / n`` where ``obj.attr`` was inferred as a boxed
 * object) cannot use the static int/float fast path. The frontend used to
 * route this to the ``__truediv__`` dunder, but a tagged int has no
 * ``__truediv__`` attribute (py_obj_getattr returns missing for tagged ints),
 * so ``obj.attr / 3`` raised ``AttributeError: __truediv__``. This generic
 * dispatcher mirrors py_obj_add/py_obj_mod: numeric operands divide as
 * doubles; everything else defers to the ``__truediv__`` dunder (real user
 * classes that define it). */
PyObject *py_obj_truediv(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for /"));
        return NULL;
    }
    int32_t at = py_type_of(a);
    int32_t bt = py_type_of(b);
    int a_num = (at == PY_TYPE_INT || at == PY_TYPE_BOOL || at == PY_TYPE_FLOAT);
    int b_num = (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL || bt == PY_TYPE_FLOAT);
    if (a_num && b_num) {
        double bd = py_float_to_f64(b);
        if (bd == 0.0) {
            py_raise(py_exc_new(PY_EXC_ZERODIVISIONERROR, "division by zero"));
            return NULL;
        }
        return py_float_from_f64(py_float_to_f64(a) / bd);
    }
    /* Non-numeric: defer to __truediv__ (e.g. a user class instance). */
    PyObject *r = py_obj_call_method1(a, "__truediv__", b);
    if (r == NULL && !py_err_occurred()) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "unsupported operand type(s) for /"));
    }
    return r;
}

PyObject *py_obj_type_name(PyObject *o) {
    if (o == NULL) return py_str_new("NoneType", 8);
    int32_t tag = py_type_of(o);
    const char *name = NULL;
    switch (tag) {
        case PY_TYPE_NONE: name = "NoneType"; break;
        case PY_TYPE_BOOL: name = "bool"; break;
        case PY_TYPE_INT: name = "int"; break;
        case PY_TYPE_FLOAT: name = "float"; break;
        case PY_TYPE_STR: name = "str"; break;
        case PY_TYPE_LIST: name = "list"; break;
        case PY_TYPE_DICT: name = "dict"; break;
        case PY_TYPE_TUPLE: name = "tuple"; break;
        case PY_TYPE_SET: name = "set"; break;
        case PY_TYPE_CLASS: name = "type"; break;
        case PY_TYPE_COMPLEX: name = "complex"; break;
        case PY_TYPE_BYTES: name = "bytes"; break;
        case PY_TYPE_BYTEARRAY: name = "bytearray"; break;
        case PY_TYPE_MEMORYVIEW: name = "memoryview"; break;
        case PY_TYPE_COROUTINE: name = "coroutine"; break;
        case PY_TYPE_CONTINUATION: name = "continuation"; break;
        case PY_TYPE_VIRTUAL_THREAD: name = "virtual_thread"; break;
        case PY_TYPE_EXC: {
            PyExceptionObject *exc = (PyExceptionObject *)o;
            PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(
                o,
                (PyObject **)&exc->exc_class
            );
            if (cls && cls->name) {
                name = cls->name;
            }
            break;
        }
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
                PyInstanceObject *inst = (PyInstanceObject *)o;
                PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(
                    o,
                    (PyObject **)&inst->cls
                );
                if (cls && cls->name) name = cls->name;
            }
            break;
    }
    if (name == NULL) name = "object";
    return py_str_new(name, (int64_t)strlen(name));
}

static PyClassObject *pcc_type_cls_none = NULL;
static PyClassObject *pcc_type_cls_bool = NULL;
static PyClassObject *pcc_type_cls_int = NULL;
static PyClassObject *pcc_type_cls_float = NULL;
static PyClassObject *pcc_type_cls_str = NULL;
static PyClassObject *pcc_type_cls_list = NULL;
static PyClassObject *pcc_type_cls_dict = NULL;
static PyClassObject *pcc_type_cls_tuple = NULL;
static PyClassObject *pcc_type_cls_set = NULL;
static PyClassObject *pcc_type_cls_type = NULL;
static PyClassObject *pcc_type_cls_complex = NULL;
static PyClassObject *pcc_type_cls_bytes = NULL;
static PyClassObject *pcc_type_cls_bytearray = NULL;
static PyClassObject *pcc_type_cls_memoryview = NULL;
static PyClassObject *pcc_type_cls_coroutine = NULL;
static PyClassObject *pcc_type_cls_object = NULL;

static PyObject *pcc_builtin_type_class(
    const char *name,
    PyClassObject **slot
) {
    if (slot == NULL) return NULL;
    if (*slot == NULL) {
        *slot = py_class_new(name, NULL, 0, NULL, 0);
    }
    if (*slot == NULL) return NULL;
    py_incref((PyObject *)*slot);
    return (PyObject *)*slot;
}

static PyObject *pcc_builtin_type_class_for_tag(int32_t tag) {
    switch (tag) {
        case PY_TYPE_NONE:
            return pcc_builtin_type_class("NoneType", &pcc_type_cls_none);
        case PY_TYPE_BOOL:
            return pcc_builtin_type_class("bool", &pcc_type_cls_bool);
        case PY_TYPE_INT:
            return pcc_builtin_type_class("int", &pcc_type_cls_int);
        case PY_TYPE_FLOAT:
            return pcc_builtin_type_class("float", &pcc_type_cls_float);
        case PY_TYPE_STR:
            return pcc_builtin_type_class("str", &pcc_type_cls_str);
        case PY_TYPE_LIST:
            return pcc_builtin_type_class("list", &pcc_type_cls_list);
        case PY_TYPE_DICT:
            return pcc_builtin_type_class("dict", &pcc_type_cls_dict);
        case PY_TYPE_TUPLE:
            return pcc_builtin_type_class("tuple", &pcc_type_cls_tuple);
        case PY_TYPE_SET:
            return pcc_builtin_type_class("set", &pcc_type_cls_set);
        case PY_TYPE_CLASS:
            return pcc_builtin_type_class("type", &pcc_type_cls_type);
        case PY_TYPE_COMPLEX:
            return pcc_builtin_type_class("complex", &pcc_type_cls_complex);
        case PY_TYPE_BYTES:
            return pcc_builtin_type_class("bytes", &pcc_type_cls_bytes);
        case PY_TYPE_BYTEARRAY:
            return pcc_builtin_type_class("bytearray", &pcc_type_cls_bytearray);
        case PY_TYPE_MEMORYVIEW:
            return pcc_builtin_type_class("memoryview", &pcc_type_cls_memoryview);
        case PY_TYPE_COROUTINE:
            return pcc_builtin_type_class("coroutine", &pcc_type_cls_coroutine);
        default:
            return pcc_builtin_type_class("object", &pcc_type_cls_object);
    }
}

PyObject *py_type_builtin(PyObject *o) {
    if (o == NULL) {
        return pcc_builtin_type_class_for_tag(PY_TYPE_NONE);
    }
    if (PY_IS_TAGGED_INT(o)) {
        return pcc_builtin_type_class_for_tag(PY_TYPE_INT);
    }

    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_EXC) {
        PyExceptionObject *exc = (PyExceptionObject *)o;
        PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(
            o,
            (PyObject **)&exc->exc_class
        );
        if (cls != NULL) {
            py_incref((PyObject *)cls);
            return (PyObject *)cls;
        }
    }
    if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
        PyInstanceObject *inst = (PyInstanceObject *)o;
        PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(
            o,
            (PyObject **)&inst->cls
        );
        if (cls != NULL) {
            py_incref((PyObject *)cls);
            return (PyObject *)cls;
        }
    }
    return pcc_builtin_type_class_for_tag(tag);
}

int64_t py_obj_len(PyObject *o) {
    if (o == NULL) return 0;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:  return py_list_len(o);
        case PY_TYPE_TUPLE: return py_tuple_len(o);
        case PY_TYPE_STR:   return py_str_len(o);
        case PY_TYPE_BYTES:
        case PY_TYPE_BYTEARRAY:
        case PY_TYPE_MEMORYVIEW:
            return py_bytes_len(o);
        case PY_TYPE_DICT:  return py_dict_len(o);
        case PY_TYPE_SET:   return py_set_len(o);
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
                int64_t handled = 0;
                int64_t user_len = py_user_len_dispatch(o, &handled);
                if (handled) return user_len;
            }
            return 0;
    }
}

PyObject *py_obj_getitem(PyObject *o, PyObject *k) {
    if (o == NULL || k == NULL) return NULL;
    int32_t tag = py_type_of(o);
    pcc_runtime_log_event_code(7, 1, tag, py_type_of(k), o);
    switch (tag) {
        case PY_TYPE_LIST: {
            int64_t idx = py_obj_index_i64(k);
            if (py_err_occurred()) return NULL;
            return py_list_get(o, idx);
        }
        case PY_TYPE_TUPLE: {
            int64_t idx = py_obj_index_i64(k);
            if (py_err_occurred()) return NULL;
            return py_tuple_get(o, idx);
        }
        case PY_TYPE_DICT:
            return py_dict_get(o, k);
        case PY_TYPE_STR:
            return py_str_index(o, k);
        case PY_TYPE_BYTES:
        case PY_TYPE_BYTEARRAY:
        case PY_TYPE_MEMORYVIEW:
            return py_bytes_getitem(o, k);
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
                return py_user_getitem_dispatch(o, k);
            }
            return NULL;
    }
}

PyObject *py_obj_slice(PyObject *o, PyObject *lo, PyObject *hi, PyObject *step) {
    if (o == NULL) return NULL;
    int32_t tag = py_type_of(o);
    pcc_runtime_log_event_code(7, 2, tag, 0, o);
    switch (tag) {
        case PY_TYPE_LIST:
            return py_list_slice(o, lo, hi, step);
        case PY_TYPE_TUPLE:
            return py_tuple_slice(o, lo, hi, step);
        case PY_TYPE_STR:
            return py_str_slice(o, lo, hi, step);
        case PY_TYPE_BYTES:
        case PY_TYPE_BYTEARRAY:
        case PY_TYPE_MEMORYVIEW:
            return py_bytes_slice(o, lo, hi, step);
        default:
            return NULL;
    }
}

int64_t py_obj_set_slice(PyObject *o, PyObject *lo, PyObject *hi,
                         PyObject *step, PyObject *replacement) {
    if (o == NULL) return -1;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:
            return py_list_set_slice(o, lo, hi, step, replacement);
        default:
            return -1;
    }
}

int64_t py_obj_del_slice(PyObject *o, PyObject *lo, PyObject *hi,
                         PyObject *step) {
    if (o == NULL) return -1;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:
            return py_list_del_slice(o, lo, hi, step);
        default:
            return -1;
    }
}

int64_t py_obj_setitem(PyObject *o, PyObject *k, PyObject *v) {
    if (o == NULL || k == NULL) return -1;
    int32_t tag = py_type_of(o);
    pcc_runtime_log_event_code(7, 3, tag, py_type_of(k), o);
    switch (tag) {
        case PY_TYPE_LIST: {
            int64_t idx = py_obj_index_i64(k);
            if (py_err_occurred()) return -1;
            py_list_set(o, idx, v);
            return 0;
        }
        case PY_TYPE_DICT:
            py_dict_set(o, k, v);
            return 0;
        case PY_TYPE_BYTEARRAY:
            return py_bytearray_setitem(o, k, v);
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
                int64_t handled = 0;
                int64_t rc = py_user_setitem_dispatch(o, k, v, &handled);
                if (handled) return rc;
            }
            return -1;
    }
}

int64_t py_obj_delitem(PyObject *o, PyObject *k) {
    if (o == NULL || k == NULL) return -1;
    int32_t tag = py_type_of(o);
    pcc_runtime_log_event_code(7, 4, tag, py_type_of(k), o);
    switch (tag) {
        case PY_TYPE_LIST: {
            int64_t idx = py_obj_index_i64(k);
            if (py_err_occurred()) return -1;
            py_list_pop(o, idx);
            return 0;
        }
        case PY_TYPE_DICT:
            return py_dict_del(o, k);
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
                int64_t handled = 0;
                int64_t rc = py_user_delitem_dispatch(o, k, &handled);
                if (handled) return rc;
            }
            return -1;
    }
}

static int is_instance_tag_d(int32_t tag) {
    return tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER;
}

static PyObject *py_obj_missing_attr(const char *name) {
    if (!py_err_occurred()) {
        py_raise(py_exc_new(
            PY_EXC_ATTRIBUTEERROR,
            name != NULL ? name : ""
        ));
    }
    return NULL;
}

static int64_t py_obj_attr_status_failed(const char *name) {
    if (!py_err_occurred()) {
        py_raise(py_exc_new(
            PY_EXC_ATTRIBUTEERROR,
            name != NULL ? name : ""
        ));
    }
    return -1;
}

PyObject *py_obj_getattr(PyObject *o, const char *name) {
    if (!o || !name) return py_obj_missing_attr(name);

    if (strcmp(name, "__class__") == 0) {
        return py_type_builtin(o);
    }

    if (PY_IS_TAGGED_INT(o)) return py_obj_missing_attr(name);
    int32_t tag = py_header(o)->type_tag;
    pcc_runtime_log_event_code(7, 5, tag, 0, o);

    if (is_instance_tag_d(tag)) {
        PyObject *result = py_instance_getattr((PyInstanceObject *)o, name);
        if (result != NULL || py_err_occurred()) return result;
        return py_obj_missing_attr(name);
    }
    if (tag == PY_TYPE_CLASS) {
        PyObject *result = py_class_getattr((PyClassObject *)o, name);
        if (result != NULL || py_err_occurred()) return result;
        return py_obj_missing_attr(name);
    }
    if (tag == PY_TYPE_FUNC) {
        PyFuncObject *f = (PyFuncObject *)o;
        if (strcmp(name, "__name__") == 0 && f->name != NULL) {
            return py_str_new(f->name, (int64_t)strlen(f->name));
        }
        if (strcmp(name, "__self__") == 0) {
            PyObject *self_obj = pcc_gc_load_ptr(o, &f->self_obj);
            if (self_obj != NULL) {
                py_incref(self_obj);
                return self_obj;
            }
        }
        return py_obj_missing_attr(name);
    }
    if (tag == PY_TYPE_WEAKREF) {
        PyObject *target = py_weakref_call(o);
        if (target == NULL || target == py_None) {
            if (target != NULL) py_decref(target);
            py_raise(py_exc_new(
                PY_EXC_REFERENCEERROR,
                "weakly-referenced object no longer exists"
            ));
            return NULL;
        }
        PyObject *result = py_obj_getattr(target, name);
        py_decref(target);
        return result;
    }
    if (tag == PY_TYPE_COROUTINE) {
        PyObject *result = NULL;
        if (strcmp(name, "__class__") == 0) {
            result = py_coroutine_class();
        }
        if (result) py_incref(result);
        if (result != NULL || py_err_occurred()) return result;
        return py_obj_missing_attr(name);
    }
    if (tag == PY_TYPE_CONTINUATION) {
        PyObject *result = NULL;
        if (strcmp(name, "__class__") == 0) {
            result = py_continuation_class();
        }
        if (result) py_incref(result);
        if (result != NULL || py_err_occurred()) return result;
        return py_obj_missing_attr(name);
    }
    if (tag == PY_TYPE_COMPLEX) {
        if (strcmp(name, "real") == 0) return py_complex_real(o);
        if (strcmp(name, "imag") == 0) return py_complex_imag(o);
        return py_obj_missing_attr(name);
    }
    if (tag == PY_TYPE_EXC) {
        PyExceptionObject *e = (PyExceptionObject *)o;
        PyObject *result = NULL;
        if (strcmp(name, "__class__") == 0) {
            result = pcc_gc_load_ptr(o, (PyObject **)&e->exc_class);
        } else if (strcmp(name, "__cause__") == 0) {
            result = pcc_gc_load_ptr(o, &e->cause);
            if (result == NULL) result = py_None;
        } else if (strcmp(name, "__context__") == 0) {
            result = pcc_gc_load_ptr(o, &e->context);
            if (result == NULL) result = py_None;
        } else if (strcmp(name, "value") == 0) {
            result = pcc_gc_load_ptr(o, &e->message);
            if (result == NULL) result = py_None;
        }
        if (result) py_incref(result);
        if (result != NULL || py_err_occurred()) return result;
        return py_obj_missing_attr(name);
    }
    return py_obj_missing_attr(name);
}

PyObject *py_obj_getattr_default(PyObject *o, const char *name) {
    if (!o || !name) return py_obj_missing_attr(name);

    if (strcmp(name, "__class__") == 0) {
        return py_type_builtin(o);
    }

    if (PY_IS_TAGGED_INT(o)) return py_obj_missing_attr(name);
    int32_t tag = py_header(o)->type_tag;
    pcc_runtime_log_event_code(7, 5, tag, 1, o);

    if (is_instance_tag_d(tag)) {
        PyObject *result = py_instance_getattr_default((PyInstanceObject *)o, name);
        if (result != NULL || py_err_occurred()) return result;
        return py_obj_missing_attr(name);
    }
    if (tag == PY_TYPE_CLASS) {
        PyObject *result = py_class_getattr((PyClassObject *)o, name);
        if (result != NULL || py_err_occurred()) return result;
        return py_obj_missing_attr(name);
    }
    return py_obj_getattr(o, name);
}

int64_t py_obj_setattr(PyObject *o, const char *name, PyObject *v) {
    if (!o || !name) return py_obj_attr_status_failed(name);
    if (PY_IS_TAGGED_INT(o)) return py_obj_attr_status_failed(name);
    int32_t tag = py_header(o)->type_tag;
    pcc_runtime_log_event_code(7, 6, tag, 0, o);

    if (is_instance_tag_d(tag)) {
        int64_t rc = py_instance_setattr((PyInstanceObject *)o, name, v);
        if (rc == 0 || py_err_occurred()) return rc;
        return py_obj_attr_status_failed(name);
    }
    if (tag == PY_TYPE_CLASS) {
        int64_t rc = py_class_setattr((PyClassObject *)o, name, v);
        if (rc == 0 || py_err_occurred()) return rc;
    }
    return py_obj_attr_status_failed(name);
}

int64_t py_obj_delattr(PyObject *o, const char *name) {
    if (!o || !name) return -1;
    if (PY_IS_TAGGED_INT(o)) return -1;
    int32_t tag = py_header(o)->type_tag;
    pcc_runtime_log_event_code(7, 7, tag, 0, o);

    if (is_instance_tag_d(tag)) {
        return py_instance_delattr((PyInstanceObject *)o, name);
    }
    if (tag == PY_TYPE_CLASS) {
        return py_class_delattr((PyClassObject *)o, name);
    }
    return -1;
}

PyObject *py_obj_call(PyObject *callable, PyObject *args, PyObject *kwargs) {
    if (!callable) return NULL;
    if (PY_IS_TAGGED_INT(callable)) return NULL;
    int32_t tag = py_header(callable)->type_tag;
    pcc_runtime_log_event_code(7, 8, tag, 0, callable);

    if (tag == PY_TYPE_CLASS) {
        PyClassObject *cls = (PyClassObject *)callable;
        PyObject *inst = py_instance_new(cls);
        if (inst == NULL) return NULL;
        PyObject *init_method = py_class_lookup(cls, "__init__");
        if (init_method != NULL) {
            PyObject *result = dispatch_call_method_with_args(
                init_method,
                inst,
                args
            );
            if (result == NULL && py_err_occurred()) {
                py_decref(inst);
                return NULL;
            }
            if (result != NULL) py_decref(result);
        }
        (void)kwargs;
        return inst;
    }
    if (tag == PY_TYPE_FUNC) {
        (void)kwargs;
        return py_func_call(callable, args);
    }
    if (tag == PY_TYPE_WEAKREF) {
        (void)args;
        (void)kwargs;
        return py_weakref_call(callable);
    }
    return NULL;
}

PyObject *py_obj_call_method1(PyObject *o, const char *name, PyObject *arg) {
    if (!o || !name) return NULL;
    PyObject *method = py_obj_getattr(o, name);
    if (method == NULL) return NULL;
    PyObject *args = py_tuple_new(2);
    if (args == NULL) return NULL;
    py_tuple_set_item(args, 0, o);
    py_tuple_set_item(args, 1, arg);
    PyObject *out = py_obj_call(method, args, py_None);
    py_decref(args);
    return out;
}

int64_t py_obj_isinstance(PyObject *o, PyObject *cls) {
    if (!o || !cls) return 0;
    if (PY_IS_TAGGED_INT(cls)) return 0;
    if (py_header(cls)->type_tag != PY_TYPE_CLASS) return 0;
    pcc_runtime_log_event_code(7, 9, py_type_of(o), py_type_of(cls), o);
    return py_isinstance(o, (PyClassObject *)cls);
}
