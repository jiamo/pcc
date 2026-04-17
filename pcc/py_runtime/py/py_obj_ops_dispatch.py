"""Phase 4c.15a: pcc-Python port of py_obj_ops_dispatch.c.

Type-tag dispatch for the simpler generic ops. The compare/hash half
stays in py_obj_ops_compare.c — porting FNV-1a + bignum cmp to pcc-
Python is subtle and deferred.

Type tags (inlined per the module-init gotcha):
    PY_TYPE_NONE     = 0
    PY_TYPE_BOOL     = 1
    PY_TYPE_INT      = 2
    PY_TYPE_FLOAT    = 3
    PY_TYPE_STR      = 4
    PY_TYPE_LIST     = 5
    PY_TYPE_DICT     = 6
    PY_TYPE_TUPLE    = 7
    PY_TYPE_SET      = 8
    PY_TYPE_CLASS    = 10
    PY_TYPE_INSTANCE = 11
    PY_TYPE_USER     = 100

Object layouts:
    PyListObject:  length@16   (i64)
    PyTupleObject: len@16      (i64)
    PyStrObject:   byte_len@16 (i64)
    PyDictObject:  size@16     (i64)
    PySetObject:   size@16     (i64)
    PyFloatObject: value@16    (f64; we read as i64 bits to test 0)
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.unsafe import (
    global_load_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
    strlen,
)

py_int_value_i64     = extern("py_int_value_i64",     (c_ptr,),                    c_int64)

py_str_len           = extern("py_str_len",           (c_ptr,),                    c_int64)
py_str_index         = extern("py_str_index",         (c_ptr, c_ptr),              c_ptr)
py_str_slice         = extern("py_str_slice",         (c_ptr, c_ptr, c_ptr, c_ptr), c_ptr)

py_list_len          = extern("py_list_len",          (c_ptr,),                    c_int64)
py_list_get          = extern("py_list_get",          (c_ptr, c_int64),            c_ptr)
py_list_set          = extern("py_list_set",          (c_ptr, c_int64, c_ptr),     c_void)
py_list_pop          = extern("py_list_pop",          (c_ptr, c_int64),            c_ptr)
py_list_slice        = extern("py_list_slice",        (c_ptr, c_ptr, c_ptr, c_ptr), c_ptr)

py_tuple_get         = extern("py_tuple_get",         (c_ptr, c_int64),            c_ptr)
py_tuple_len         = extern("py_tuple_len",         (c_ptr,),                    c_int64)
py_tuple_slice       = extern("py_tuple_slice",       (c_ptr, c_ptr, c_ptr, c_ptr), c_ptr)

py_dict_get          = extern("py_dict_get",          (c_ptr, c_ptr),              c_ptr)
py_dict_set          = extern("py_dict_set",          (c_ptr, c_ptr, c_ptr),       c_void)
py_dict_del          = extern("py_dict_del",          (c_ptr, c_ptr),              c_int64)
py_dict_len          = extern("py_dict_len",          (c_ptr,),                    c_int64)

py_set_len           = extern("py_set_len",           (c_ptr,),                    c_int64)

py_class_lookup      = extern("py_class_lookup",      (c_ptr, c_ptr),              c_ptr)
py_instance_new      = extern("py_instance_new",      (c_ptr,),                    c_ptr)
py_instance_getattr  = extern("py_instance_getattr",  (c_ptr, c_ptr),              c_ptr)
py_instance_setattr  = extern("py_instance_setattr",  (c_ptr, c_ptr, c_ptr),       c_int64)
py_isinstance        = extern("py_isinstance",        (c_ptr, c_ptr),              c_int64)

py_decref            = extern("py_decref",            (c_ptr,),                    c_void)
py_incref            = extern("py_incref",            (c_ptr,),                    c_void)


def _cstr_is_dunder_class(s) -> int:
    if strlen(s) != 9:
        return 0
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if load_i8(s, 2) != 99:
        return 0
    if load_i8(s, 3) != 108:
        return 0
    if load_i8(s, 4) != 97:
        return 0
    if load_i8(s, 5) != 115:
        return 0
    if load_i8(s, 6) != 115:
        return 0
    if load_i8(s, 7) != 95:
        return 0
    if load_i8(s, 8) != 95:
        return 0
    return 1


def _cstr_is_dunder_cause(s) -> int:
    if strlen(s) != 9:
        return 0
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if load_i8(s, 2) != 99:
        return 0
    if load_i8(s, 3) != 97:
        return 0
    if load_i8(s, 4) != 117:
        return 0
    if load_i8(s, 5) != 115:
        return 0
    if load_i8(s, 6) != 101:
        return 0
    if load_i8(s, 7) != 95:
        return 0
    if load_i8(s, 8) != 95:
        return 0
    return 1


def _cstr_is_dunder_context(s) -> int:
    if strlen(s) != 11:
        return 0
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if load_i8(s, 2) != 99:
        return 0
    if load_i8(s, 3) != 111:
        return 0
    if load_i8(s, 4) != 110:
        return 0
    if load_i8(s, 5) != 116:
        return 0
    if load_i8(s, 6) != 101:
        return 0
    if load_i8(s, 7) != 120:
        return 0
    if load_i8(s, 8) != 116:
        return 0
    if load_i8(s, 9) != 95:
        return 0
    if load_i8(s, 10) != 95:
        return 0
    return 1


def _type_of(o) -> int:
    if is_tagged_int(o) != 0:
        return 2          # PY_TYPE_INT
    return load_i32(o, 8)


@c_abi_export("py_obj_truthy")
def py_obj_truthy(o) -> int:
    if ptr_is_null(o) != 0:
        return 0
    if ptr_eq(o, global_load_ptr("py_None")) != 0:
        return 0
    if ptr_eq(o, global_load_ptr("py_False")) != 0:
        return 0
    if ptr_eq(o, global_load_ptr("py_True")) != 0:
        return 1
    if is_tagged_int(o) != 0:
        if py_int_value_i64(o) != 0:
            return 1
        return 0
    tag: int = load_i32(o, 8)
    if tag == 2:                      # PY_TYPE_INT
        if py_int_value_i64(o) != 0:
            return 1
        return 0
    if tag == 3:                      # PY_TYPE_FLOAT — read i64 bits at offset 16
        if load_i64(o, 16) != 0:
            return 1
        return 0
    if tag == 5:                      # PY_TYPE_LIST — length@16
        if load_i64(o, 16) != 0:
            return 1
        return 0
    if tag == 7:                      # PY_TYPE_TUPLE — len@16
        if load_i64(o, 16) != 0:
            return 1
        return 0
    if tag == 4:                      # PY_TYPE_STR — byte_len@16
        if load_i64(o, 16) != 0:
            return 1
        return 0
    if tag == 6:                      # PY_TYPE_DICT — size@16
        if load_i64(o, 16) != 0:
            return 1
        return 0
    if tag == 8:                      # PY_TYPE_SET — size@16
        if load_i64(o, 16) != 0:
            return 1
        return 0
    return 1


@c_abi_export("py_obj_type_tag")
def py_obj_type_tag(o) -> int:
    if ptr_is_null(o) != 0:
        return -1
    return _type_of(o)


@c_abi_export("py_obj_len")
def py_obj_len(o) -> int:
    if ptr_is_null(o) != 0:
        return 0
    tag: int = _type_of(o)
    if tag == 5:
        return py_list_len(o)
    if tag == 7:
        return py_tuple_len(o)
    if tag == 4:
        return py_str_len(o)
    if tag == 6:
        return py_dict_len(o)
    if tag == 8:
        return py_set_len(o)
    return 0


@c_abi_export("py_obj_getitem")
def py_obj_getitem(o, k):
    if ptr_is_null(o) != 0:
        return null()
    if ptr_is_null(k) != 0:
        return null()
    tag: int = _type_of(o)
    if tag == 5:
        if _type_of(k) == 2:
            return py_list_get(o, py_int_value_i64(k))
        return null()
    if tag == 7:
        if _type_of(k) == 2:
            return py_tuple_get(o, py_int_value_i64(k))
        return null()
    if tag == 6:
        return py_dict_get(o, k)
    if tag == 4:
        return py_str_index(o, k)
    return null()


@c_abi_export("py_obj_slice")
def py_obj_slice(o, lo, hi, step):
    if ptr_is_null(o) != 0:
        return null()
    tag: int = _type_of(o)
    if tag == 5:
        return py_list_slice(o, lo, hi, step)
    if tag == 7:
        return py_tuple_slice(o, lo, hi, step)
    if tag == 4:
        return py_str_slice(o, lo, hi, step)
    return null()


@c_abi_export("py_obj_setitem")
def py_obj_setitem(o, k, v) -> int:
    if ptr_is_null(o) != 0:
        return -1
    if ptr_is_null(k) != 0:
        return -1
    tag: int = _type_of(o)
    if tag == 5:
        if _type_of(k) == 2:
            py_list_set(o, py_int_value_i64(k), v)
            return 0
        return -1
    if tag == 6:
        py_dict_set(o, k, v)
        return 0
    return -1


@c_abi_export("py_obj_delitem")
def py_obj_delitem(o, k) -> int:
    if ptr_is_null(o) != 0:
        return -1
    if ptr_is_null(k) != 0:
        return -1
    tag: int = _type_of(o)
    if tag == 5:
        if _type_of(k) == 2:
            popped = py_list_pop(o, py_int_value_i64(k))
            if ptr_is_null(popped) == 0:
                py_decref(popped)
            return 0
        return -1
    if tag == 6:
        return py_dict_del(o, k)
    return -1


def _is_instance_tag(tag: int) -> int:
    if tag == 11:                     # PY_TYPE_INSTANCE
        return 1
    if tag >= 100:                    # PY_TYPE_USER
        return 1
    return 0


@c_abi_export("py_obj_getattr")
def py_obj_getattr(o, name):
    if ptr_is_null(o) != 0:
        return null()
    if ptr_is_null(name) != 0:
        return null()
    if is_tagged_int(o) != 0:
        return null()
    tag: int = load_i32(o, 8)
    if _is_instance_tag(tag) != 0:
        return py_instance_getattr(o, name)
    if tag == 10:                     # PY_TYPE_CLASS
        return py_class_lookup(o, name)
    if tag == 12:                     # PY_TYPE_EXC
        result = null()
        if _cstr_is_dunder_class(name) != 0:
            result = load_ptr(o, 16)
        elif _cstr_is_dunder_cause(name) != 0:
            result = load_ptr(o, 32)
            if ptr_is_null(result) != 0:
                result = global_load_ptr("py_None")
        elif _cstr_is_dunder_context(name) != 0:
            result = load_ptr(o, 40)
            if ptr_is_null(result) != 0:
                result = global_load_ptr("py_None")
        if ptr_is_null(result) == 0:
            py_incref(result)
        return result
    return null()


@c_abi_export("py_obj_setattr")
def py_obj_setattr(o, name, v) -> int:
    if ptr_is_null(o) != 0:
        return -1
    if ptr_is_null(name) != 0:
        return -1
    if is_tagged_int(o) != 0:
        return -1
    tag: int = load_i32(o, 8)
    if _is_instance_tag(tag) != 0:
        return py_instance_setattr(o, name, v)
    return -1


@c_abi_export("py_obj_call")
def py_obj_call(callable, args, kwargs):
    if ptr_is_null(callable) != 0:
        return null()
    if is_tagged_int(callable) != 0:
        return null()
    tag: int = load_i32(callable, 8)
    if tag == 10:                     # PY_TYPE_CLASS
        return py_instance_new(callable)
    return null()


@c_abi_export("py_obj_isinstance")
def py_obj_isinstance(o, cls) -> int:
    if ptr_is_null(o) != 0:
        return 0
    if ptr_is_null(cls) != 0:
        return 0
    if is_tagged_int(cls) != 0:
        return 0
    cls_tag: int = load_i32(cls, 8)
    if cls_tag != 10:                 # PY_TYPE_CLASS
        return 0
    return py_isinstance(o, cls)
