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
        case PY_TYPE_DICT:  return ((PyDictObject *)o)->size != 0;
        case PY_TYPE_SET:   return ((PySetObject *)o)->size != 0;
        default:
            return 1;
    }
}

int64_t py_obj_type_tag(PyObject *o) {
    if (o == NULL) return -1;
    return py_type_of(o);
}

int64_t py_obj_len(PyObject *o) {
    if (o == NULL) return 0;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:  return py_list_len(o);
        case PY_TYPE_TUPLE: return py_tuple_len(o);
        case PY_TYPE_STR:   return py_str_len(o);
        case PY_TYPE_DICT:  return py_dict_len(o);
        case PY_TYPE_SET:   return py_set_len(o);
        default:
            return 0;
    }
}

PyObject *py_obj_getitem(PyObject *o, PyObject *k) {
    if (o == NULL || k == NULL) return NULL;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:
            if (py_type_of(k) == PY_TYPE_INT) {
                return py_list_get(o, py_int_value_i64(k));
            }
            return NULL;
        case PY_TYPE_TUPLE:
            if (py_type_of(k) == PY_TYPE_INT) {
                return py_tuple_get(o, py_int_value_i64(k));
            }
            return NULL;
        case PY_TYPE_DICT:
            return py_dict_get(o, k);
        case PY_TYPE_STR:
            return py_str_index(o, k);
        default:
            return NULL;
    }
}

PyObject *py_obj_slice(PyObject *o, PyObject *lo, PyObject *hi, PyObject *step) {
    if (o == NULL) return NULL;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:
            return py_list_slice(o, lo, hi, step);
        case PY_TYPE_TUPLE:
            return py_tuple_slice(o, lo, hi, step);
        case PY_TYPE_STR:
            return py_str_slice(o, lo, hi, step);
        default:
            return NULL;
    }
}

int64_t py_obj_setitem(PyObject *o, PyObject *k, PyObject *v) {
    if (o == NULL || k == NULL) return -1;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:
            if (py_type_of(k) == PY_TYPE_INT) {
                py_list_set(o, py_int_value_i64(k), v);
                return 0;
            }
            return -1;
        case PY_TYPE_DICT:
            py_dict_set(o, k, v);
            return 0;
        default:
            return -1;
    }
}

int64_t py_obj_delitem(PyObject *o, PyObject *k) {
    if (o == NULL || k == NULL) return -1;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:
            if (py_type_of(k) == PY_TYPE_INT) {
                py_list_pop(o, py_int_value_i64(k));
                return 0;
            }
            return -1;
        case PY_TYPE_DICT:
            return py_dict_del(o, k);
        default:
            return -1;
    }
}

static int is_instance_tag_d(int32_t tag) {
    return tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER;
}

PyObject *py_obj_getattr(PyObject *o, const char *name) {
    if (!o || !name) return NULL;
    if (PY_IS_TAGGED_INT(o)) return NULL;
    int32_t tag = py_header(o)->type_tag;

    if (is_instance_tag_d(tag)) {
        return py_instance_getattr((PyInstanceObject *)o, name);
    }
    if (tag == PY_TYPE_CLASS) {
        return py_class_lookup((PyClassObject *)o, name);
    }
    if (tag == PY_TYPE_EXC) {
        PyExceptionObject *e = (PyExceptionObject *)o;
        PyObject *result = NULL;
        if (strcmp(name, "__class__") == 0) {
            result = (PyObject *)e->exc_class;
        } else if (strcmp(name, "__cause__") == 0) {
            result = e->cause ? e->cause : py_None;
        } else if (strcmp(name, "__context__") == 0) {
            result = e->context ? e->context : py_None;
        }
        if (result) py_incref(result);
        return result;
    }
    return NULL;
}

int64_t py_obj_setattr(PyObject *o, const char *name, PyObject *v) {
    if (!o || !name) return -1;
    if (PY_IS_TAGGED_INT(o)) return -1;
    int32_t tag = py_header(o)->type_tag;

    if (is_instance_tag_d(tag)) {
        return py_instance_setattr((PyInstanceObject *)o, name, v);
    }
    return -1;
}

PyObject *py_obj_call(PyObject *callable, PyObject *args, PyObject *kwargs) {
    if (!callable) return NULL;
    if (PY_IS_TAGGED_INT(callable)) return NULL;
    int32_t tag = py_header(callable)->type_tag;

    if (tag == PY_TYPE_CLASS) {
        PyClassObject *cls = (PyClassObject *)callable;
        PyObject *inst = py_instance_new(cls);
        (void)args;
        (void)kwargs;
        return inst;
    }
    return NULL;
}

int64_t py_obj_isinstance(PyObject *o, PyObject *cls) {
    if (!o || !cls) return 0;
    if (PY_IS_TAGGED_INT(cls)) return 0;
    if (py_header(cls)->type_tag != PY_TYPE_CLASS) return 0;
    return py_isinstance(o, (PyClassObject *)cls);
}
