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
    PY_TYPE_COMPLEX  = 16
    PY_TYPE_BYTES    = 17
    PY_TYPE_BYTEARRAY = 18
    PY_TYPE_MEMORYVIEW = 19
    PY_TYPE_COROUTINE = 20
    PY_TYPE_CONTINUATION = 29
    PY_TYPE_VIRTUAL_THREAD = 30
    PY_TYPE_USER     = 100

Object layouts:
    PyListObject:  length@16   (i64)
    PyTupleObject: len@16      (i64)
    PyStrObject:   byte_len@16 (i64)
    PyDictObject:  size@16     (i64)
    PySetObject:   size@16     (i64)
    PyFloatObject: value@16    (f64; we read as i64 bits to test 0)
"""
from pcc.extern import extern, c_abi_export, c_int32, c_ptr, c_int64, c_void, c_double
from pcc.unsafe import (
    cstr,
    define_global_ptr_null,
    free,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i64,
    strlen,
)

py_int_value_i64     = extern("py_int_value_i64",     (c_ptr,),                    c_int64)
py_obj_index_i64     = extern("py_obj_index_i64",     (c_ptr,),                    c_int64)

py_str_new           = extern("py_str_new",           (c_ptr, c_int64),            c_ptr)
py_str_len           = extern("py_str_len",           (c_ptr,),                    c_int64)
py_str_index         = extern("py_str_index",         (c_ptr, c_ptr),              c_ptr)
py_list_len          = extern("py_list_len",          (c_ptr,),                    c_int64)
py_list_get          = extern("py_list_get",          (c_ptr, c_int64),            c_ptr)
py_list_set          = extern("py_list_set",          (c_ptr, c_int64, c_ptr),     c_void)
py_list_pop          = extern("py_list_pop",          (c_ptr, c_int64),            c_ptr)
py_list_del_slice    = extern("py_list_del_slice",    (c_ptr, c_ptr, c_ptr, c_ptr), c_int64)
py_list_concat       = extern("py_list_concat",       (c_ptr, c_ptr),              c_ptr)

py_tuple_get         = extern("py_tuple_get",         (c_ptr, c_int64),            c_ptr)
py_tuple_len         = extern("py_tuple_len",         (c_ptr,),                    c_int64)
py_tuple_new         = extern("py_tuple_new",         (c_int64,),                  c_ptr)
py_tuple_set_item    = extern("py_tuple_set_item",    (c_ptr, c_int64, c_ptr),     c_void)
py_tuple_concat      = extern("py_tuple_concat",      (c_ptr, c_ptr),              c_ptr)

py_dict_get          = extern("py_dict_get",          (c_ptr, c_ptr),              c_ptr)
py_dict_set          = extern("py_dict_set",          (c_ptr, c_ptr, c_ptr),       c_void)
py_dict_del          = extern("py_dict_del",          (c_ptr, c_ptr),              c_int64)
py_dict_len          = extern("py_dict_len",          (c_ptr,),                    c_int64)

py_set_len           = extern("py_set_len",           (c_ptr,),                    c_int64)

py_class_new         = extern("py_class_new",         (c_ptr, c_ptr, c_int32, c_ptr, c_int32), c_ptr)
py_class_lookup      = extern("py_class_lookup",      (c_ptr, c_ptr),              c_ptr)
py_class_getattr     = extern("py_class_getattr",     (c_ptr, c_ptr),              c_ptr)
py_class_setattr     = extern("py_class_setattr",     (c_ptr, c_ptr, c_ptr),       c_int64)
py_class_delattr     = extern("py_class_delattr",     (c_ptr, c_ptr),              c_int64)
py_instance_new      = extern("py_instance_new",      (c_ptr,),                    c_ptr)
py_instance_getattr  = extern("py_instance_getattr",  (c_ptr, c_ptr),              c_ptr)
py_instance_getattr_default = extern("py_instance_getattr_default", (c_ptr, c_ptr), c_ptr)
py_instance_setattr  = extern("py_instance_setattr",  (c_ptr, c_ptr, c_ptr),       c_int64)
py_instance_delattr  = extern("py_instance_delattr",  (c_ptr, c_ptr),              c_int64)
py_isinstance        = extern("py_isinstance",        (c_ptr, c_ptr),              c_int64)
py_user_len_dispatch = extern("py_user_len_dispatch", (c_ptr, c_ptr),              c_int64)
py_user_bool_dispatch = extern("py_user_bool_dispatch", (c_ptr, c_ptr),            c_int64)
py_user_getitem_dispatch = extern("py_user_getitem_dispatch", (c_ptr, c_ptr),      c_ptr)
py_user_setitem_dispatch = extern("py_user_setitem_dispatch", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64)
py_user_delitem_dispatch = extern("py_user_delitem_dispatch", (c_ptr, c_ptr, c_ptr), c_int64)
py_func_call         = extern("py_func_call",         (c_ptr, c_ptr),              c_ptr)
py_weakref_call      = extern("py_weakref_call",      (c_ptr,),                    c_ptr)
py_exc_new           = extern("py_exc_new",           (c_int64, c_ptr),            c_ptr)
py_raise             = extern("py_raise",             (c_ptr,),                    c_void)
py_err_occurred      = extern("py_err_occurred",      (),                          c_int64)
py_int_add           = extern("py_int_add",           (c_ptr, c_ptr),              c_ptr)
py_int_sub           = extern("py_int_sub",           (c_ptr, c_ptr),              c_ptr)
py_int_mul           = extern("py_int_mul",           (c_ptr, c_ptr),              c_ptr)
py_float_add         = extern("py_float_add",         (c_ptr, c_ptr),              c_ptr)
py_float_sub         = extern("py_float_sub",         (c_ptr, c_ptr),              c_ptr)
py_float_mul         = extern("py_float_mul",         (c_ptr, c_ptr),              c_ptr)
py_complex_add       = extern("py_complex_add",       (c_ptr, c_ptr),              c_ptr)
py_str_repeat        = extern("py_str_repeat",        (c_ptr, c_ptr),              c_ptr)
py_list_repeat       = extern("py_list_repeat",       (c_ptr, c_ptr),              c_ptr)
py_tuple_repeat      = extern("py_tuple_repeat",      (c_ptr, c_ptr),              c_ptr)
py_float_to_f64      = extern("py_float_to_f64",      (c_ptr,),                    c_double)
py_float_from_f64    = extern("py_float_from_f64",    (c_double,),                 c_ptr)
py_str_concat        = extern("py_str_concat",        (c_ptr, c_ptr),              c_ptr)
py_complex_real      = extern("py_complex_real",      (c_ptr,),                    c_ptr)
py_complex_imag      = extern("py_complex_imag",      (c_ptr,),                    c_ptr)
py_coroutine_class   = extern("py_coroutine_class",   (),                          c_ptr)
py_continuation_class = extern("py_continuation_class", (),                         c_ptr)
py_bytes_len         = extern("py_bytes_len",         (c_ptr,),                    c_int64)
py_bytes_getitem     = extern("py_bytes_getitem",     (c_ptr, c_ptr),              c_ptr)
py_bytes_concat      = extern("py_bytes_concat",      (c_ptr, c_ptr),              c_ptr)
py_bytearray_setitem = extern("py_bytearray_setitem", (c_ptr, c_ptr, c_ptr),       c_int64)

py_decref            = extern("py_decref",            (c_ptr,),                    c_void)
py_incref            = extern("py_incref",            (c_ptr,),                    c_void)
pcc_gc_load_ptr      = extern("pcc_gc_load_ptr",      (c_ptr, c_ptr),              c_ptr)
pcc_gc_note_relocation_read = extern(
    "pcc_gc_note_relocation_read",
    (c_ptr,),
    c_ptr,
)
pcc_runtime_log_event_code = extern(
    "pcc_runtime_log_event_code", (c_int32, c_int32, c_int64, c_int64, c_ptr), c_void,
)


define_global_ptr_null("pcc_type_cls_none")
define_global_ptr_null("pcc_type_cls_bool")
define_global_ptr_null("pcc_type_cls_int")
define_global_ptr_null("pcc_type_cls_float")
define_global_ptr_null("pcc_type_cls_str")
define_global_ptr_null("pcc_type_cls_list")
define_global_ptr_null("pcc_type_cls_dict")
define_global_ptr_null("pcc_type_cls_tuple")
define_global_ptr_null("pcc_type_cls_set")
define_global_ptr_null("pcc_type_cls_type")
define_global_ptr_null("pcc_type_cls_complex")
define_global_ptr_null("pcc_type_cls_bytes")
define_global_ptr_null("pcc_type_cls_bytearray")
define_global_ptr_null("pcc_type_cls_memoryview")
define_global_ptr_null("pcc_type_cls_coroutine")
define_global_ptr_null("pcc_type_cls_object")


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


def _cstr_is_dunder_name(s) -> int:
    if strlen(s) != 8:
        return 0
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if load_i8(s, 2) != 110:
        return 0
    if load_i8(s, 3) != 97:
        return 0
    if load_i8(s, 4) != 109:
        return 0
    if load_i8(s, 5) != 101:
        return 0
    if load_i8(s, 6) != 95:
        return 0
    if load_i8(s, 7) != 95:
        return 0
    return 1


def _cstr_is_dunder_self(s) -> int:
    if strlen(s) != 8:
        return 0
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if load_i8(s, 2) != 115:
        return 0
    if load_i8(s, 3) != 101:
        return 0
    if load_i8(s, 4) != 108:
        return 0
    if load_i8(s, 5) != 102:
        return 0
    if load_i8(s, 6) != 95:
        return 0
    if load_i8(s, 7) != 95:
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


def _cstr_is_value(s) -> int:
    if strlen(s) != 5:
        return 0
    if load_i8(s, 0) != 118:
        return 0
    if load_i8(s, 1) != 97:
        return 0
    if load_i8(s, 2) != 108:
        return 0
    if load_i8(s, 3) != 117:
        return 0
    if load_i8(s, 4) != 101:
        return 0
    return 1


def _cstr_is_real(s) -> int:
    if strlen(s) != 4:
        return 0
    if load_i8(s, 0) != 114:
        return 0
    if load_i8(s, 1) != 101:
        return 0
    if load_i8(s, 2) != 97:
        return 0
    if load_i8(s, 3) != 108:
        return 0
    return 1


def _cstr_is_imag(s) -> int:
    if strlen(s) != 4:
        return 0
    if load_i8(s, 0) != 105:
        return 0
    if load_i8(s, 1) != 109:
        return 0
    if load_i8(s, 2) != 97:
        return 0
    if load_i8(s, 3) != 103:
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
    if tag == 17 or tag == 18 or tag == 19:
        if py_bytes_len(o) != 0:
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
    if _is_instance_tag(tag) != 0:
        handled = malloc(8)
        if ptr_is_null(handled) == 0:
            store_i64(handled, 0, 0)
            user_bool: int = py_user_bool_dispatch(o, handled)
            if load_i64(handled, 0) != 0:
                free(handled)
                if user_bool != 0:
                    return 1
                return 0
            store_i64(handled, 0, 0)
            user_len: int = py_user_len_dispatch(o, handled)
            if load_i64(handled, 0) != 0:
                free(handled)
                if user_len != 0:
                    return 1
                return 0
            free(handled)
    return 1


@c_abi_export("py_obj_type_tag")
def py_obj_type_tag(o) -> int:
    if ptr_is_null(o) != 0:
        return -1
    return _type_of(o)


@c_abi_export("py_obj_add")
def py_obj_add(a, b):
    if ptr_is_null(a) != 0 or ptr_is_null(b) != 0:
        py_raise(py_exc_new(3, cstr("unsupported operand type(s) for +")))
        return null()
    at: int = _type_of(a)
    bt: int = _type_of(b)
    if (at == 2 or at == 1) and (bt == 2 or bt == 1):
        return py_int_add(a, b)
    if at == 16 or bt == 16:
        return py_complex_add(a, b)
    if at == 3 or bt == 3:
        return py_float_add(a, b)
    if at == 4 and bt == 4:
        return py_str_concat(a, b)
    if (at == 17 or at == 18) and (bt == 17 or bt == 18):
        return py_bytes_concat(a, b)
    if at == 5 and bt == 5:
        return py_list_concat(a, b)
    if at == 7 and bt == 7:
        return py_tuple_concat(a, b)
    py_raise(py_exc_new(3, cstr("unsupported operand type(s) for +")))
    return null()


@c_abi_export("py_obj_sub")
def py_obj_sub(a, b):
    # Generic ``a - b`` for dynamically-typed operands. Mirrors py_obj_add:
    # int/bool -> py_int_sub (bignum); any float -> py_float_sub (coerces the
    # other numeric operand). Subtraction is numeric-only in Python. Fixes
    # boxed-float ``-`` (e.g. ``obj.attr - n`` where attr is a float) which fell
    # to the i64 path and misread the boxed pointer.
    if ptr_is_null(a) != 0 or ptr_is_null(b) != 0:
        py_raise(py_exc_new(3, cstr("unsupported operand type(s) for -")))
        return null()
    at: int = _type_of(a)
    bt: int = _type_of(b)
    if (at == 2 or at == 1) and (bt == 2 or bt == 1):
        return py_int_sub(a, b)
    if (at == 3 or at == 2 or at == 1) and (bt == 3 or bt == 2 or bt == 1):
        return py_float_sub(a, b)
    py_raise(py_exc_new(3, cstr("unsupported operand type(s) for -")))
    return null()


@c_abi_export("py_obj_mul")
def py_obj_mul(a, b):
    # Generic ``a * b`` for dynamically-typed operands. int/bool -> py_int_mul;
    # any-float-numeric -> py_float_mul; sequence * int -> repetition (str via
    # py_str_repeat which takes a PyObject count, list/tuple via py_*_repeat
    # which take an i64 count -> unbox with py_int_value_i64). Fixes boxed-float
    # ``*`` (``obj.attr * n``) which fell to the i64 path.
    if ptr_is_null(a) != 0 or ptr_is_null(b) != 0:
        py_raise(py_exc_new(3, cstr("unsupported operand type(s) for *")))
        return null()
    at: int = _type_of(a)
    bt: int = _type_of(b)
    if (at == 2 or at == 1) and (bt == 2 or bt == 1):
        return py_int_mul(a, b)
    if (at == 3 or at == 2 or at == 1) and (bt == 3 or bt == 2 or bt == 1):
        return py_float_mul(a, b)
    if at == 4 and (bt == 1 or bt == 2):
        return py_str_repeat(a, b)
    if bt == 4 and (at == 1 or at == 2):
        return py_str_repeat(b, a)
    if at == 5 and (bt == 1 or bt == 2):
        return py_list_repeat(a, py_int_value_i64(b))
    if bt == 5 and (at == 1 or at == 2):
        return py_list_repeat(b, py_int_value_i64(a))
    if at == 7 and (bt == 1 or bt == 2):
        return py_tuple_repeat(a, py_int_value_i64(b))
    if bt == 7 and (at == 1 or at == 2):
        return py_tuple_repeat(b, py_int_value_i64(a))
    py_raise(py_exc_new(3, cstr("unsupported operand type(s) for *")))
    return null()


@c_abi_export("py_obj_truediv")
def py_obj_truediv(a, b):
    # Python true division (``a / b``) for dynamically-typed operands. Mirrors
    # py_obj_truediv in py_obj_ops_dispatch.c: numeric operands divide as
    # doubles (always producing a float, like CPython); anything else defers to
    # the ``__truediv__`` dunder. A tagged int has no ``__truediv__`` attribute,
    # so routing DynType ``/`` straight to the dunder raised AttributeError.
    if ptr_is_null(a) != 0 or ptr_is_null(b) != 0:
        py_raise(py_exc_new(3, cstr("unsupported operand type(s) for /")))
        return null()
    at: int = _type_of(a)
    bt: int = _type_of(b)
    a_num: int = 0
    if at == 2 or at == 1 or at == 3:
        a_num = 1
    b_num: int = 0
    if bt == 2 or bt == 1 or bt == 3:
        b_num = 1
    if a_num == 1 and b_num == 1:
        bd: float = py_float_to_f64(b)
        if bd == 0.0:
            py_raise(py_exc_new(9, cstr("division by zero")))
            return null()
        ad: float = py_float_to_f64(a)
        return py_float_from_f64(ad / bd)
    # Non-numeric: defer to __truediv__ (e.g. a user class instance).
    r = py_obj_call_method1(a, cstr("__truediv__"), b)
    if ptr_is_null(r) != 0 and py_err_occurred() == 0:
        py_raise(py_exc_new(3, cstr("unsupported operand type(s) for /")))
    return r


def _type_name_cstr_for_tag(tag: int):
    if tag == 0:
        return cstr("NoneType")
    if tag == 1:
        return cstr("bool")
    if tag == 2:
        return cstr("int")
    if tag == 3:
        return cstr("float")
    if tag == 4:
        return cstr("str")
    if tag == 5:
        return cstr("list")
    if tag == 6:
        return cstr("dict")
    if tag == 7:
        return cstr("tuple")
    if tag == 8:
        return cstr("set")
    if tag == 10:
        return cstr("type")
    if tag == 16:
        return cstr("complex")
    if tag == 17:
        return cstr("bytes")
    if tag == 18:
        return cstr("bytearray")
    if tag == 19:
        return cstr("memoryview")
    if tag == 20:
        return cstr("coroutine")
    if tag == 29:
        return cstr("continuation")
    if tag == 30:
        return cstr("virtual_thread")
    return cstr("object")


@c_abi_export("py_obj_type_name")
def py_obj_type_name(o):
    if ptr_is_null(o) != 0:
        name = cstr("NoneType")
        return py_str_new(name, strlen(name))
    tag: int = _type_of(o)
    if _is_instance_tag(tag) != 0:
        cls = pcc_gc_load_ptr(o, ptr_add(o, 16))
        if ptr_is_null(cls) == 0:
            cls_name = load_ptr(cls, 16)
            if ptr_is_null(cls_name) == 0:
                return py_str_new(cls_name, strlen(cls_name))
    if tag == 12:                     # PY_TYPE_EXC
        cls = pcc_gc_load_ptr(o, ptr_add(o, 16))
        if ptr_is_null(cls) == 0:
            cls_name = load_ptr(cls, 16)
            if ptr_is_null(cls_name) == 0:
                return py_str_new(cls_name, strlen(cls_name))
    name = _type_name_cstr_for_tag(tag)
    return py_str_new(name, strlen(name))


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
    if tag == 17 or tag == 18 or tag == 19:
        return py_bytes_len(o)
    if tag == 6:
        return py_dict_len(o)
    if tag == 8:
        return py_set_len(o)
    if _is_instance_tag(tag) != 0:
        return py_user_len_dispatch(o, null())
    return 0


@c_abi_export("py_obj_getitem")
def py_obj_getitem(o, k):
    if ptr_is_null(o) != 0:
        return null()
    if ptr_is_null(k) != 0:
        return null()
    tag: int = _type_of(o)
    pcc_runtime_log_event_code(7, 1, tag, _type_of(k), o)
    if tag == 5:
        idx: int = py_obj_index_i64(k)
        if py_err_occurred() != 0:
            return null()
        return py_list_get(o, idx)
    if tag == 7:
        idx: int = py_obj_index_i64(k)
        if py_err_occurred() != 0:
            return null()
        return py_tuple_get(o, idx)
    if tag == 6:
        return py_dict_get(o, k)
    if tag == 4:
        return py_str_index(o, k)
    if tag == 17 or tag == 18 or tag == 19:
        return py_bytes_getitem(o, k)
    if _is_instance_tag(tag) != 0:
        return py_user_getitem_dispatch(o, k)
    return null()


@c_abi_export("py_obj_del_slice")
def py_obj_del_slice(o, lo, hi, step) -> int:
    if ptr_is_null(o) != 0:
        return -1
    tag: int = _type_of(o)
    if tag == 5:
        return py_list_del_slice(o, lo, hi, step)
    return -1


@c_abi_export("py_obj_setitem")
def py_obj_setitem(o, k, v) -> int:
    if ptr_is_null(o) != 0:
        return -1
    if ptr_is_null(k) != 0:
        return -1
    tag: int = _type_of(o)
    pcc_runtime_log_event_code(7, 3, tag, _type_of(k), o)
    if tag == 5:
        idx: int = py_obj_index_i64(k)
        if py_err_occurred() != 0:
            return -1
        py_list_set(o, idx, v)
        return 0
    if tag == 6:
        py_dict_set(o, k, v)
        return 0
    if tag == 18:
        return py_bytearray_setitem(o, k, v)
    if _is_instance_tag(tag) != 0:
        return py_user_setitem_dispatch(o, k, v, null())
    return -1


@c_abi_export("py_obj_delitem")
def py_obj_delitem(o, k) -> int:
    if ptr_is_null(o) != 0:
        return -1
    if ptr_is_null(k) != 0:
        return -1
    tag: int = _type_of(o)
    pcc_runtime_log_event_code(7, 4, tag, _type_of(k), o)
    if tag == 5:
        idx: int = py_obj_index_i64(k)
        if py_err_occurred() != 0:
            return -1
        popped = py_list_pop(o, idx)
        if ptr_is_null(popped) == 0:
            py_decref(popped)
        return 0
    if tag == 6:
        return py_dict_del(o, k)
    if _is_instance_tag(tag) != 0:
        return py_user_delitem_dispatch(o, k, null())
    return -1


def _is_instance_tag(tag: int) -> int:
    if tag == 11:                     # PY_TYPE_INSTANCE
        return 1
    if tag >= 100:                    # PY_TYPE_USER
        return 1
    return 0


def _return_builtin_type(cls):
    if ptr_is_null(cls) != 0:
        return null()
    py_incref(cls)
    return cls


def _builtin_type_class_for_tag(tag: int):
    cls = null()
    if tag == 0:                      # PY_TYPE_NONE
        cls = global_load_ptr("pcc_type_cls_none")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("NoneType"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_none", cls)
        return _return_builtin_type(cls)
    if tag == 1:                      # PY_TYPE_BOOL
        cls = global_load_ptr("pcc_type_cls_bool")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("bool"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_bool", cls)
        return _return_builtin_type(cls)
    if tag == 2:                      # PY_TYPE_INT
        cls = global_load_ptr("pcc_type_cls_int")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("int"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_int", cls)
        return _return_builtin_type(cls)
    if tag == 3:                      # PY_TYPE_FLOAT
        cls = global_load_ptr("pcc_type_cls_float")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("float"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_float", cls)
        return _return_builtin_type(cls)
    if tag == 4:                      # PY_TYPE_STR
        cls = global_load_ptr("pcc_type_cls_str")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("str"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_str", cls)
        return _return_builtin_type(cls)
    if tag == 5:                      # PY_TYPE_LIST
        cls = global_load_ptr("pcc_type_cls_list")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("list"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_list", cls)
        return _return_builtin_type(cls)
    if tag == 6:                      # PY_TYPE_DICT
        cls = global_load_ptr("pcc_type_cls_dict")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("dict"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_dict", cls)
        return _return_builtin_type(cls)
    if tag == 7:                      # PY_TYPE_TUPLE
        cls = global_load_ptr("pcc_type_cls_tuple")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("tuple"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_tuple", cls)
        return _return_builtin_type(cls)
    if tag == 8:                      # PY_TYPE_SET
        cls = global_load_ptr("pcc_type_cls_set")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("set"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_set", cls)
        return _return_builtin_type(cls)
    if tag == 10:                     # PY_TYPE_CLASS
        cls = global_load_ptr("pcc_type_cls_type")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("type"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_type", cls)
        return _return_builtin_type(cls)
    if tag == 16:                     # PY_TYPE_COMPLEX
        cls = global_load_ptr("pcc_type_cls_complex")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("complex"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_complex", cls)
        return _return_builtin_type(cls)
    if tag == 17:                     # PY_TYPE_BYTES
        cls = global_load_ptr("pcc_type_cls_bytes")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("bytes"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_bytes", cls)
        return _return_builtin_type(cls)
    if tag == 18:                     # PY_TYPE_BYTEARRAY
        cls = global_load_ptr("pcc_type_cls_bytearray")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("bytearray"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_bytearray", cls)
        return _return_builtin_type(cls)
    if tag == 19:                     # PY_TYPE_MEMORYVIEW
        cls = global_load_ptr("pcc_type_cls_memoryview")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("memoryview"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_memoryview", cls)
        return _return_builtin_type(cls)
    if tag == 20:                     # PY_TYPE_COROUTINE
        cls = global_load_ptr("pcc_type_cls_coroutine")
        if ptr_is_null(cls) != 0:
            cls = py_class_new(cstr("coroutine"), null(), 0, null(), 0)
            if ptr_is_null(cls) == 0:
                global_store_ptr("pcc_type_cls_coroutine", cls)
        return _return_builtin_type(cls)
    cls = global_load_ptr("pcc_type_cls_object")
    if ptr_is_null(cls) != 0:
        cls = py_class_new(cstr("object"), null(), 0, null(), 0)
        if ptr_is_null(cls) == 0:
            global_store_ptr("pcc_type_cls_object", cls)
    return _return_builtin_type(cls)


@c_abi_export("py_type_builtin")
def py_type_builtin(o):
    if ptr_is_null(o) != 0:
        return _builtin_type_class_for_tag(0)
    if is_tagged_int(o) != 0:
        return _builtin_type_class_for_tag(2)
    tag: int = _type_of(o)
    if tag == 12:                     # PY_TYPE_EXC
        cls = pcc_gc_load_ptr(o, ptr_add(o, 16))
        if ptr_is_null(cls) == 0:
            py_incref(cls)
            return cls
    if _is_instance_tag(tag) != 0:
        cls = pcc_gc_load_ptr(o, ptr_add(o, 16))
        if ptr_is_null(cls) == 0:
            py_incref(cls)
            return cls
    return _builtin_type_class_for_tag(tag)


def _raise_attribute_error(name):
    if py_err_occurred() == 0:
        msg = name
        if ptr_is_null(msg) != 0:
            msg = cstr("")
        exc = py_exc_new(6, msg)       # PY_EXC_ATTRIBUTEERROR
        py_raise(exc)
    return null()


def _raise_attribute_status(name) -> int:
    _raise_attribute_error(name)
    return -1


@c_abi_export("py_obj_getattr")
def py_obj_getattr(o, name):
    if ptr_is_null(o) != 0:
        return _raise_attribute_error(name)
    if ptr_is_null(name) != 0:
        return _raise_attribute_error(name)
    if _cstr_is_dunder_class(name) != 0:
        return py_type_builtin(o)
    if is_tagged_int(o) != 0:
        return _raise_attribute_error(name)

    tag: int = load_i32(o, 8)
    pcc_runtime_log_event_code(7, 5, tag, 0, o)

    if _is_instance_tag(tag) != 0:
        result = py_instance_getattr(o, name)
        if ptr_is_null(result) == 0:
            return result
        if py_err_occurred() != 0:
            return result
        return _raise_attribute_error(name)
    if tag == 10:                     # PY_TYPE_CLASS
        result = py_class_getattr(o, name)
        if ptr_is_null(result) == 0:
            return result
        if py_err_occurred() != 0:
            return result
        return _raise_attribute_error(name)
    if tag == 9:                      # PY_TYPE_FUNC
        func_name = load_ptr(o, 32)
        if _cstr_is_dunder_name(name) != 0 and ptr_is_null(func_name) == 0:
            return py_str_new(func_name, strlen(func_name))
        if _cstr_is_dunder_self(name) != 0:
            self_obj = pcc_gc_load_ptr(o, ptr_add(o, 40))
            if ptr_is_null(self_obj) == 0:
                py_incref(self_obj)
                return self_obj
        return _raise_attribute_error(name)
    if tag == 21:                     # PY_TYPE_WEAKREF
        target = py_weakref_call(o)
        if ptr_is_null(target) != 0:
            exc = py_exc_new(
                18,                   # PY_EXC_REFERENCEERROR
                cstr("weakly-referenced object no longer exists"),
            )
            py_raise(exc)
            return null()
        if ptr_eq(target, global_load_ptr("py_None")) != 0:
            py_decref(target)
            exc = py_exc_new(
                18,
                cstr("weakly-referenced object no longer exists"),
            )
            py_raise(exc)
            return null()
        result = py_obj_getattr(target, name)
        py_decref(target)
        return result
    if tag == 20:                     # PY_TYPE_COROUTINE
        result = null()
        if _cstr_is_dunder_class(name) != 0:
            result = py_coroutine_class()
        if ptr_is_null(result) == 0:
            py_incref(result)
            return result
        if py_err_occurred() != 0:
            return result
        return _raise_attribute_error(name)
    if tag == 29:                     # PY_TYPE_CONTINUATION
        result = null()
        if _cstr_is_dunder_class(name) != 0:
            result = py_continuation_class()
        if ptr_is_null(result) == 0:
            py_incref(result)
            return result
        if py_err_occurred() != 0:
            return result
        return _raise_attribute_error(name)
    if tag == 16:                     # PY_TYPE_COMPLEX
        if _cstr_is_real(name) != 0:
            return py_complex_real(o)
        if _cstr_is_imag(name) != 0:
            return py_complex_imag(o)
        return _raise_attribute_error(name)
    if tag == 12:                     # PY_TYPE_EXC
        result = null()
        if _cstr_is_dunder_class(name) != 0:
            result = pcc_gc_load_ptr(o, ptr_add(o, 16))
        elif _cstr_is_dunder_cause(name) != 0:
            result = pcc_gc_load_ptr(o, ptr_add(o, 32))
            if ptr_is_null(result) != 0:
                result = global_load_ptr("py_None")
        elif _cstr_is_dunder_context(name) != 0:
            result = pcc_gc_load_ptr(o, ptr_add(o, 40))
            if ptr_is_null(result) != 0:
                result = global_load_ptr("py_None")
        elif _cstr_is_value(name) != 0:
            result = pcc_gc_load_ptr(o, ptr_add(o, 24))
            if ptr_is_null(result) != 0:
                result = global_load_ptr("py_None")
        if ptr_is_null(result) == 0:
            py_incref(result)
            return result
        if py_err_occurred() != 0:
            return result
        return _raise_attribute_error(name)
    return _raise_attribute_error(name)


@c_abi_export("py_obj_getattr_default")
def py_obj_getattr_default(o, name):
    if ptr_is_null(o) != 0:
        return _raise_attribute_error(name)
    if ptr_is_null(name) != 0:
        return _raise_attribute_error(name)
    if _cstr_is_dunder_class(name) != 0:
        return py_type_builtin(o)
    if is_tagged_int(o) != 0:
        return _raise_attribute_error(name)

    tag: int = load_i32(o, 8)
    pcc_runtime_log_event_code(7, 5, tag, 1, o)

    if _is_instance_tag(tag) != 0:
        result = py_instance_getattr_default(o, name)
        if ptr_is_null(result) == 0:
            return result
        if py_err_occurred() != 0:
            return result
        return _raise_attribute_error(name)
    if tag == 10:                     # PY_TYPE_CLASS
        result = py_class_getattr(o, name)
        if ptr_is_null(result) == 0:
            return result
        if py_err_occurred() != 0:
            return result
        return _raise_attribute_error(name)
    return py_obj_getattr(o, name)


@c_abi_export("py_obj_setattr")
def py_obj_setattr(o, name, v) -> int:
    if ptr_is_null(o) != 0:
        return _raise_attribute_status(name)
    if ptr_is_null(name) != 0:
        return _raise_attribute_status(name)
    if is_tagged_int(o) != 0:
        return _raise_attribute_status(name)
    tag: int = load_i32(o, 8)
    pcc_runtime_log_event_code(7, 6, tag, 0, o)

    if _is_instance_tag(tag) != 0:
        rc: int = py_instance_setattr(o, name, v)
        if rc == 0:
            return rc
        if py_err_occurred() != 0:
            return rc
        return _raise_attribute_status(name)
    if tag == 10:                     # PY_TYPE_CLASS
        rc: int = py_class_setattr(o, name, v)
        if rc == 0:
            return rc
        if py_err_occurred() != 0:
            return rc
    return _raise_attribute_status(name)


@c_abi_export("py_obj_delattr")
def py_obj_delattr(o, name) -> int:
    if ptr_is_null(o) != 0:
        return -1
    if ptr_is_null(name) != 0:
        return -1
    if is_tagged_int(o) != 0:
        return -1
    tag: int = load_i32(o, 8)
    pcc_runtime_log_event_code(7, 7, tag, 0, o)

    if _is_instance_tag(tag) != 0:
        return py_instance_delattr(o, name)
    if tag == 10:                     # PY_TYPE_CLASS
        return py_class_delattr(o, name)
    return -1


@c_abi_export("py_obj_call")
def py_obj_call(callable, args, kwargs):
    if ptr_is_null(callable) != 0:
        return null()
    if is_tagged_int(callable) != 0:
        return null()
    tag: int = load_i32(callable, 8)
    pcc_runtime_log_event_code(7, 8, tag, 0, callable)

    if tag == 10:                     # PY_TYPE_CLASS
        inst = py_instance_new(callable)
        if ptr_is_null(inst) != 0:
            return null()
        init_method = py_class_lookup(callable, cstr("__init__"))
        if ptr_is_null(init_method) == 0:
            if is_tagged_int(init_method) == 0:
                if load_i32(init_method, 8) == 9:  # PY_TYPE_FUNC
                    n: int = 0
                    if ptr_is_null(args) == 0:
                        n = py_tuple_len(args)
                    full_args = py_tuple_new(n + 1)
                    if ptr_is_null(full_args) != 0:
                        py_decref(inst)
                        return null()
                    py_tuple_set_item(full_args, 0, inst)
                    i: int = 0
                    while i < n:
                        item = py_tuple_get(args, i)
                        py_tuple_set_item(full_args, i + 1, item)
                        py_decref(item)
                        i = i + 1
                    out = py_func_call(init_method, full_args)
                    py_decref(full_args)
                    if ptr_is_null(out) != 0:
                        if py_err_occurred() != 0:
                            py_decref(inst)
                            return null()
                    else:
                        py_decref(out)
        return inst
    if tag == 9:                      # PY_TYPE_FUNC
        return py_func_call(callable, args)
    if tag == 21:                     # PY_TYPE_WEAKREF
        return py_weakref_call(callable)
    return null()


@c_abi_export("py_obj_call_method1")
def py_obj_call_method1(o, name, arg):
    if ptr_is_null(o) != 0:
        return null()
    if ptr_is_null(name) != 0:
        return null()
    method = py_obj_getattr(o, name)
    if ptr_is_null(method) != 0:
        return null()
    args = py_tuple_new(2)
    if ptr_is_null(args) != 0:
        return null()
    py_tuple_set_item(args, 0, o)
    py_tuple_set_item(args, 1, arg)
    out = py_obj_call(method, args, global_load_ptr("py_None"))
    py_decref(args)
    return out


@c_abi_export("py_obj_isinstance")
def py_obj_isinstance(o, cls) -> int:
    if ptr_is_null(o) != 0:
        return 0
    if ptr_is_null(cls) != 0:
        return 0
    if is_tagged_int(cls) != 0:
        return 0
    if load_i32(cls, 8) != 10:         # PY_TYPE_CLASS
        return 0
    pcc_runtime_log_event_code(7, 9, _type_of(o), _type_of(cls), o)
    return py_isinstance(o, cls)
