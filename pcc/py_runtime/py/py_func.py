"""pcc-Python port of py_func.c.

Function object layout:
    offset  0   PyObjectHeader
    offset 16   PyNativeFuncEntry
    offset 24   captures tuple
    offset 32   borrowed const char* name, nullable
    offset 40   bound self object, nullable
    total size: 48 bytes
"""
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_DICT,
    PY_TYPE_FUNC,
    PY_TYPE_NONE,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
)

from pcc.extern import extern, c_abi_export, c_int32, c_int64, c_ptr, c_void
from pcc.unsafe import (
    call_ptr2,
    cstr,
    global_load_ptr,
    global_store_ptr,
    ptr_add,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
    store_ptr,
    strlen,
)

py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_contains = extern("py_dict_contains", (c_ptr, c_ptr), c_int64)
py_dict_del = extern("py_dict_del", (c_ptr, c_ptr), c_int64)
py_dict_get = extern("py_dict_get", (c_ptr, c_ptr), c_ptr)
py_dict_len = extern("py_dict_len", (c_ptr,), c_int64)
py_call_merge_kwargs = extern("py_call_merge_kwargs", (c_ptr, c_ptr), c_ptr)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_obj_getattr = extern("py_obj_getattr", (c_ptr, c_ptr), c_ptr)
py_obj_truthy = extern("py_obj_truthy", (c_ptr,), c_int64)
py_obj_setattr = extern("py_obj_setattr", (c_ptr, c_ptr, c_ptr), c_int64)
py_dict_update = extern("py_dict_update", (c_ptr, c_ptr), c_void)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_eq = extern("py_str_eq", (c_ptr, c_ptr), c_int64)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_runtime_error_if_unset = extern(
    "py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr
)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_gc_track = extern("py_gc_track", (c_ptr,), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
py_class_new = extern(
    "py_class_new",
    (c_ptr, c_ptr, c_int32, c_ptr, c_int32),
    c_ptr,
)
py_instance_new = extern("py_instance_new", (c_ptr,), c_ptr)
py_func_new_bound_raw = extern(
    "py_func_new_bound",
    (c_ptr, c_ptr, c_ptr, c_ptr),
    c_ptr,
)


def _checked_func(obj):
    if ptr_is_null(obj):
        return null()
    if is_tagged_int(obj):
        return null()
    if load_i32(obj, 8) != PY_TYPE_FUNC:
        return null()
    return obj


def _is_none_or_null(obj) -> int:
    if ptr_is_null(obj):
        return 1
    if is_tagged_int(obj):
        return 0
    if load_i32(obj, 8) == PY_TYPE_NONE:
        return 1
    return 0


def _is_tuple(obj) -> int:
    if ptr_is_null(obj):
        return 0
    if is_tagged_int(obj):
        return 0
    if load_i32(obj, 8) == PY_TYPE_TUPLE:
        return 1
    return 0


def _is_dict(obj) -> int:
    if ptr_is_null(obj):
        return 0
    if is_tagged_int(obj):
        return 0
    if load_i32(obj, 8) == PY_TYPE_DICT:
        return 1
    return 0


def _func_type_error(message):
    exc = py_exc_new(3, message)
    py_raise(exc)
    if ptr_is_null(exc) == 0:
        py_decref(exc)
    return null()


def _func_runtime_error_if_unset(helper_name, message):
    if py_err_occurred() != 0:
        return null()
    return py_runtime_error_if_unset(helper_name, message)


def _kwargs_empty(kwargs) -> int:
    if _is_none_or_null(kwargs) != 0:
        return 1
    if _is_dict(kwargs) == 0:
        return 0
    if py_dict_len(kwargs) == 0:
        return 1
    return 0


def _signature_valid(sig) -> int:
    if _is_tuple(sig) == 0:
        return 0
    sig_len: int = py_tuple_len(sig)
    if sig_len < 5:
        return 0
    # ``magic`` is released on every exit below, so obtain the owned reference
    # promised by py_tuple_get instead of borrowing the tuple slot directly.
    magic = py_tuple_get(sig, 0)
    if ptr_is_null(magic):
        return 0
    if is_tagged_int(magic) != 0:
        py_decref(magic)
        return 0
    if load_i32(magic, 8) != PY_TYPE_STR:
        py_decref(magic)
        return 0
    magic_len: int = load_i64(magic, 16)
    if magic_len != 25:
        py_decref(magic)
        return 0
    if load_i8(magic, 40) != 95:
        py_decref(magic)
        return 0
    if load_i8(magic, 41) != 95:
        py_decref(magic)
        return 0
    if load_i8(magic, 42) != 112:
        py_decref(magic)
        return 0
    if load_i8(magic, 43) != 99:
        py_decref(magic)
        return 0
    if load_i8(magic, 44) != 99:
        py_decref(magic)
        return 0
    if load_i8(magic, 45) != 95:
        py_decref(magic)
        return 0
    if load_i8(magic, 46) != 102:
        py_decref(magic)
        return 0
    if load_i8(magic, 47) != 117:
        py_decref(magic)
        return 0
    if load_i8(magic, 48) != 110:
        py_decref(magic)
        return 0
    if load_i8(magic, 49) != 99:
        py_decref(magic)
        return 0
    if load_i8(magic, 50) != 95:
        py_decref(magic)
        return 0
    if load_i8(magic, 51) != 115:
        py_decref(magic)
        return 0
    if load_i8(magic, 52) != 105:
        py_decref(magic)
        return 0
    if load_i8(magic, 53) != 103:
        py_decref(magic)
        return 0
    if load_i8(magic, 54) != 110:
        py_decref(magic)
        return 0
    if load_i8(magic, 55) != 97:
        py_decref(magic)
        return 0
    if load_i8(magic, 56) != 116:
        py_decref(magic)
        return 0
    if load_i8(magic, 57) != 117:
        py_decref(magic)
        return 0
    if load_i8(magic, 58) != 114:
        py_decref(magic)
        return 0
    if load_i8(magic, 59) != 101:
        py_decref(magic)
        return 0
    if load_i8(magic, 60) != 95:
        py_decref(magic)
        return 0
    if load_i8(magic, 61) != 118:
        py_decref(magic)
        return 0
    if load_i8(magic, 62) != 49:
        py_decref(magic)
        return 0
    if load_i8(magic, 63) != 95:
        py_decref(magic)
        return 0
    if load_i8(magic, 64) != 95:
        py_decref(magic)
        return 0
    py_decref(magic)
    return 1


def _code_set_owned_attr(code, name, value) -> int:
    if ptr_is_null(value):
        return -1
    rc: int = py_obj_setattr(code, name, value)
    py_decref(value)
    return rc


@c_abi_export("py_func_attach_code_metadata")
def py_func_attach_code_metadata(func, signature, name) -> int:
    if ptr_is_null(func) or is_tagged_int(func):
        return -1
    if load_i32(func, 8) != PY_TYPE_FUNC or _signature_valid(signature) == 0:
        return -1

    names = py_tuple_get(signature, 1)
    kinds = py_tuple_get(signature, 2)
    if ptr_is_null(names) or ptr_is_null(kinds):
        if ptr_is_null(names) == 0:
            py_decref(names)
        if ptr_is_null(kinds) == 0:
            py_decref(kinds)
        return -1
    count: int = py_tuple_len(names)
    if py_tuple_len(kinds) != count:
        py_decref(names)
        py_decref(kinds)
        return -1

    argcount: int = 0
    posonlyargcount: int = 0
    kwonlyargcount: int = 0
    flags: int = 0
    i: int = 0
    while i < count:
        kind_obj = py_tuple_get(kinds, i)
        if ptr_is_null(kind_obj):
            py_decref(names)
            py_decref(kinds)
            return -1
        kind: int = py_int_value_i64(kind_obj)
        py_decref(kind_obj)
        if kind == 0 or kind == 1:
            argcount += 1
        if kind == 1:
            posonlyargcount += 1
        if kind == 2:
            kwonlyargcount += 1
        if kind == 3:
            flags = flags | 4
        if kind == 4:
            flags = flags | 8
        i += 1

    varnames = py_tuple_new(count)
    if ptr_is_null(varnames):
        py_decref(names)
        py_decref(kinds)
        return -1
    out_index: int = 0
    group: int = 0
    while group < 4:
        i = 0
        while i < count:
            kind_obj = py_tuple_get(kinds, i)
            if ptr_is_null(kind_obj):
                py_decref(varnames)
                py_decref(names)
                py_decref(kinds)
                return -1
            kind = py_int_value_i64(kind_obj)
            py_decref(kind_obj)
            matches: int = 0
            if group == 0 and (kind == 0 or kind == 1):
                matches = 1
            elif group == 1 and kind == 2:
                matches = 1
            elif group == 2 and kind == 3:
                matches = 1
            elif group == 3 and kind == 4:
                matches = 1
            if matches != 0:
                arg_name = py_tuple_get(names, i)
                if ptr_is_null(arg_name):
                    py_decref(varnames)
                    py_decref(names)
                    py_decref(kinds)
                    return -1
                py_tuple_set_item(varnames, out_index, arg_name)
                py_decref(arg_name)
                out_index += 1
            i += 1
        group += 1

    code_class = global_load_ptr("py_func_code_class_cache")
    if ptr_is_null(code_class):
        code_class = py_class_new(cstr("code"), null(), 0, null(), 0)
        if ptr_is_null(code_class):
            py_decref(varnames)
            py_decref(names)
            py_decref(kinds)
            return -1
        pcc_gc_pin(code_class)
        global_store_ptr("py_func_code_class_cache", code_class)
    code = py_instance_new(code_class)
    if ptr_is_null(code):
        py_decref(varnames)
        py_decref(names)
        py_decref(kinds)
        return -1

    if _code_set_owned_attr(code, cstr("co_argcount"), py_int_from_i64(argcount)) != 0:
        py_decref(varnames)
        py_decref(code)
        py_decref(names)
        py_decref(kinds)
        return -1
    if (
        _code_set_owned_attr(
            code, cstr("co_posonlyargcount"), py_int_from_i64(posonlyargcount)
        )
        != 0
    ):
        py_decref(varnames)
        py_decref(code)
        py_decref(names)
        py_decref(kinds)
        return -1
    if (
        _code_set_owned_attr(
            code, cstr("co_kwonlyargcount"), py_int_from_i64(kwonlyargcount)
        )
        != 0
    ):
        py_decref(varnames)
        py_decref(code)
        py_decref(names)
        py_decref(kinds)
        return -1
    if _code_set_owned_attr(code, cstr("co_flags"), py_int_from_i64(flags)) != 0:
        py_decref(varnames)
        py_decref(code)
        py_decref(names)
        py_decref(kinds)
        return -1
    if _code_set_owned_attr(code, cstr("co_varnames"), varnames) != 0:
        py_decref(code)
        py_decref(names)
        py_decref(kinds)
        return -1
    name_value = py_str_new(name, strlen(name))
    if _code_set_owned_attr(code, cstr("co_name"), name_value) != 0:
        py_decref(code)
        py_decref(names)
        py_decref(kinds)
        return -1
    rc: int = py_obj_setattr(func, cstr("__code__"), code)
    py_decref(code)
    py_decref(names)
    py_decref(kinds)
    return rc


def _signature_from_captures(captures, out_actual_slot):
    store_ptr(out_actual_slot, 0, captures)
    if _is_tuple(captures) == 0:
        return null()
    captures_len: int = py_tuple_len(captures)
    if captures_len != 2:
        return null()
    candidate = py_tuple_get(captures, 1)
    if _signature_valid(candidate) == 0:
        if ptr_is_null(candidate) == 0:
            py_decref(candidate)
        return null()
    inner = py_tuple_get(captures, 0)
    if ptr_is_null(inner):
        py_decref(candidate)
        return null()
    store_ptr(out_actual_slot, 0, inner)
    return candidate


@c_abi_export("py_func_get_code_metadata")
def py_func_get_code_metadata(func):
    if ptr_is_null(func) or is_tagged_int(func):
        return null()
    if load_i32(func, 8) != PY_TYPE_FUNC:
        return null()
    captures = pcc_gc_load_ptr(func, ptr_add(func, 64))
    if _is_tuple(captures) == 0 or py_tuple_len(captures) != 2:
        return null()
    signature = py_tuple_get(captures, 1)
    if _signature_valid(signature) == 0:
        if ptr_is_null(signature) == 0:
            py_decref(signature)
        return null()
    name = load_ptr(func, 72)
    rc: int = py_func_attach_code_metadata(func, signature, name)
    py_decref(signature)
    if rc != 0:
        return null()
    attrs = pcc_gc_load_ptr(func, ptr_add(func, 88))
    if ptr_is_null(attrs):
        return null()
    key = py_str_new(cstr("__code__"), 8)
    if ptr_is_null(key):
        return null()
    code = py_dict_get(attrs, key)
    py_decref(key)
    return code


@c_abi_export("py_func_get_defaults_metadata")
def py_func_get_defaults_metadata(func):
    if ptr_is_null(func) or is_tagged_int(func):
        return null()
    if load_i32(func, 8) != PY_TYPE_FUNC:
        return null()
    captures = pcc_gc_load_ptr(func, ptr_add(func, 64))
    if _is_tuple(captures) == 0 or py_tuple_len(captures) != 2:
        return null()
    signature = py_tuple_get(captures, 1)
    if _signature_valid(signature) == 0:
        if ptr_is_null(signature) == 0:
            py_decref(signature)
        return null()

    kinds = py_tuple_get(signature, 2)
    has_defaults = py_tuple_get(signature, 3)
    defaults = py_tuple_get(signature, 4)
    if (
        ptr_is_null(kinds)
        or ptr_is_null(has_defaults)
        or ptr_is_null(defaults)
    ):
        if ptr_is_null(kinds) == 0:
            py_decref(kinds)
        if ptr_is_null(has_defaults) == 0:
            py_decref(has_defaults)
        if ptr_is_null(defaults) == 0:
            py_decref(defaults)
        py_decref(signature)
        return null()
    count: int = py_tuple_len(kinds)
    if py_tuple_len(has_defaults) != count or py_tuple_len(defaults) != count:
        py_decref(kinds)
        py_decref(has_defaults)
        py_decref(defaults)
        py_decref(signature)
        return null()

    default_count: int = 0
    i: int = 0
    while i < count:
        kind_obj = py_tuple_get(kinds, i)
        has_default_obj = py_tuple_get(has_defaults, i)
        if ptr_is_null(kind_obj) or ptr_is_null(has_default_obj):
            if ptr_is_null(kind_obj) == 0:
                py_decref(kind_obj)
            if ptr_is_null(has_default_obj) == 0:
                py_decref(has_default_obj)
            py_decref(kinds)
            py_decref(has_defaults)
            py_decref(defaults)
            py_decref(signature)
            return null()
        kind: int = py_int_value_i64(kind_obj)
        has_default: int = py_obj_truthy(has_default_obj)
        py_decref(kind_obj)
        py_decref(has_default_obj)
        if (kind == 0 or kind == 1) and has_default != 0:
            default_count += 1
        i += 1

    if default_count == 0:
        out = global_load_ptr("py_None")
        py_incref(out)
    else:
        out = py_tuple_new(default_count)
        if ptr_is_null(out):
            py_decref(kinds)
            py_decref(has_defaults)
            py_decref(defaults)
            py_decref(signature)
            return null()
        out_index: int = 0
        i = 0
        while i < count:
            kind_obj = py_tuple_get(kinds, i)
            has_default_obj = py_tuple_get(has_defaults, i)
            if ptr_is_null(kind_obj) or ptr_is_null(has_default_obj):
                if ptr_is_null(kind_obj) == 0:
                    py_decref(kind_obj)
                if ptr_is_null(has_default_obj) == 0:
                    py_decref(has_default_obj)
                py_decref(out)
                py_decref(kinds)
                py_decref(has_defaults)
                py_decref(defaults)
                py_decref(signature)
                return null()
            kind = py_int_value_i64(kind_obj)
            has_default = py_obj_truthy(has_default_obj)
            py_decref(kind_obj)
            py_decref(has_default_obj)
            if (kind == 0 or kind == 1) and has_default != 0:
                default_obj = py_tuple_get(defaults, i)
                if ptr_is_null(default_obj):
                    py_decref(out)
                    py_decref(kinds)
                    py_decref(has_defaults)
                    py_decref(defaults)
                    py_decref(signature)
                    return null()
                py_tuple_set_item(out, out_index, default_obj)
                py_decref(default_obj)
                out_index += 1
            i += 1

    py_decref(kinds)
    py_decref(has_defaults)
    py_decref(defaults)
    py_decref(signature)
    if py_obj_setattr(func, cstr("__defaults__"), out) != 0:
        py_decref(out)
        return null()
    return out


def _copy_varargs(args, start: int, nargs: int):
    count: int = nargs - start
    if count < 0:
        count = 0
    out = py_tuple_new(count)
    if ptr_is_null(out):
        return null()
    i: int = 0
    while i < count:
        item = py_tuple_get(args, start + i)
        if ptr_is_null(item):
            py_decref(out)
            return null()
        py_tuple_set_item(out, i, item)
        py_decref(item)
        i = i + 1
    return out


def _tuple_borrow_known(t, i: int):
    return pcc_gc_load_ptr(t, ptr_add(t, 24 + i * 8))


def _copy_varargs_known(args, start: int, nargs: int):
    count: int = nargs - start
    if count < 0:
        count = 0
    out = py_tuple_new(count)
    if ptr_is_null(out):
        return null()
    i: int = 0
    while i < count:
        item = _tuple_borrow_known(args, start + i)
        if ptr_is_null(item):
            py_decref(out)
            return null()
        py_tuple_set_item(out, i, item)
        i = i + 1
    return out


def _cleanup_signature_parts(names, kinds, has_defaults, defaults) -> None:
    if ptr_is_null(names) == 0:
        py_decref(names)
    if ptr_is_null(kinds) == 0:
        py_decref(kinds)
    if ptr_is_null(has_defaults) == 0:
        py_decref(has_defaults)
    if ptr_is_null(defaults) == 0:
        py_decref(defaults)


def _bind_signature(sig, args_tuple, kwargs):
    names = py_tuple_get(sig, 1)
    kinds = py_tuple_get(sig, 2)
    has_defaults = py_tuple_get(sig, 3)
    defaults = py_tuple_get(sig, 4)
    if (
        ptr_is_null(names)
        or ptr_is_null(kinds)
        or ptr_is_null(has_defaults)
        or ptr_is_null(defaults)
    ):
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return null()

    nformals: int = py_tuple_len(names)
    kinds_len: int = py_tuple_len(kinds)
    has_defaults_len: int = py_tuple_len(has_defaults)
    defaults_len: int = py_tuple_len(defaults)
    if kinds_len != nformals:
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return _func_type_error(cstr("invalid native function signature"))
    if has_defaults_len != nformals:
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return _func_type_error(cstr("invalid native function signature"))
    if defaults_len != nformals:
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return _func_type_error(cstr("invalid native function signature"))

    args = args_tuple
    made_args: int = 0
    if _is_none_or_null(args) != 0:
        args = py_tuple_new(0)
        made_args = 1
    if _is_tuple(args) == 0:
        if made_args != 0:
            py_decref(args)
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return _func_type_error(cstr("native function args must be a tuple"))

    remaining = py_call_merge_kwargs(null(), kwargs)
    if ptr_is_null(remaining):
        if made_args != 0:
            py_decref(args)
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return null()

    bound = py_tuple_new(nformals)
    if ptr_is_null(bound):
        py_decref(remaining)
        if made_args != 0:
            py_decref(args)
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return null()

    nargs: int = py_tuple_len(args)
    pos_index: int = 0
    saw_varkw: int = 0
    i: int = 0
    while i < nformals:
        name = py_tuple_get(names, i)
        kind_obj = py_tuple_get(kinds, i)
        has_default_obj = py_tuple_get(has_defaults, i)
        default_obj = py_tuple_get(defaults, i)
        if (
            ptr_is_null(name)
            or ptr_is_null(kind_obj)
            or ptr_is_null(has_default_obj)
            or ptr_is_null(default_obj)
        ):
            if ptr_is_null(name) == 0:
                py_decref(name)
            if ptr_is_null(kind_obj) == 0:
                py_decref(kind_obj)
            if ptr_is_null(has_default_obj) == 0:
                py_decref(has_default_obj)
            if ptr_is_null(default_obj) == 0:
                py_decref(default_obj)
            py_decref(bound)
            py_decref(remaining)
            if made_args != 0:
                py_decref(args)
            _cleanup_signature_parts(names, kinds, has_defaults, defaults)
            return null()

        kind: int = py_int_value_i64(kind_obj)
        has_default: int = py_obj_truthy(has_default_obj)

        if kind == 3:  # PCC_FUNC_KIND_VARARGS
            varargs = _copy_varargs(args, pos_index, nargs)
            if ptr_is_null(varargs):
                py_decref(name)
                py_decref(kind_obj)
                py_decref(has_default_obj)
                py_decref(default_obj)
                py_decref(bound)
                py_decref(remaining)
                if made_args != 0:
                    py_decref(args)
                _cleanup_signature_parts(names, kinds, has_defaults, defaults)
                return null()
            py_tuple_set_item(bound, i, varargs)
            py_decref(varargs)
            pos_index = nargs
            py_decref(name)
            py_decref(kind_obj)
            py_decref(has_default_obj)
            py_decref(default_obj)
            i = i + 1
            continue

        if kind == 4:  # PCC_FUNC_KIND_VARKW
            py_tuple_set_item(bound, i, remaining)
            saw_varkw = 1
            py_decref(name)
            py_decref(kind_obj)
            py_decref(has_default_obj)
            py_decref(default_obj)
            i = i + 1
            continue

        if kind != 2 and pos_index < nargs:  # PCC_FUNC_KIND_KW_ONLY
            if (
                kind != 1  # PCC_FUNC_KIND_POS_ONLY
                and py_dict_contains(remaining, name) != 0
            ):
                py_decref(name)
                py_decref(kind_obj)
                py_decref(has_default_obj)
                py_decref(default_obj)
                py_decref(bound)
                py_decref(remaining)
                if made_args != 0:
                    py_decref(args)
                _cleanup_signature_parts(names, kinds, has_defaults, defaults)
                return _func_type_error(
                    cstr("native function got multiple values for argument")
                )
            item = py_tuple_get(args, pos_index)
            pos_index = pos_index + 1
            if ptr_is_null(item):
                py_decref(name)
                py_decref(kind_obj)
                py_decref(has_default_obj)
                py_decref(default_obj)
                py_decref(bound)
                py_decref(remaining)
                if made_args != 0:
                    py_decref(args)
                _cleanup_signature_parts(names, kinds, has_defaults, defaults)
                return null()
            py_tuple_set_item(bound, i, item)
            py_decref(item)
            py_decref(name)
            py_decref(kind_obj)
            py_decref(has_default_obj)
            py_decref(default_obj)
            i = i + 1
            continue

        if kind != 1 and py_dict_contains(remaining, name) != 0:
            item2 = py_dict_get(remaining, name)
            py_dict_del(remaining, name)
            if ptr_is_null(item2):
                py_decref(name)
                py_decref(kind_obj)
                py_decref(has_default_obj)
                py_decref(default_obj)
                py_decref(bound)
                py_decref(remaining)
                if made_args != 0:
                    py_decref(args)
                _cleanup_signature_parts(names, kinds, has_defaults, defaults)
                return null()
            py_tuple_set_item(bound, i, item2)
            py_decref(item2)
            py_decref(name)
            py_decref(kind_obj)
            py_decref(has_default_obj)
            py_decref(default_obj)
            i = i + 1
            continue

        if has_default != 0:
            py_tuple_set_item(bound, i, default_obj)
            py_decref(name)
            py_decref(kind_obj)
            py_decref(has_default_obj)
            py_decref(default_obj)
            i = i + 1
            continue

        py_decref(name)
        py_decref(kind_obj)
        py_decref(has_default_obj)
        py_decref(default_obj)
        py_decref(bound)
        py_decref(remaining)
        if made_args != 0:
            py_decref(args)
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return _func_type_error(cstr("missing required native function argument"))

    if pos_index < nargs:
        py_decref(bound)
        py_decref(remaining)
        if made_args != 0:
            py_decref(args)
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return _func_type_error(
            cstr("native function got too many positional arguments")
        )

    if saw_varkw == 0 and py_dict_len(remaining) != 0:
        py_decref(bound)
        py_decref(remaining)
        if made_args != 0:
            py_decref(args)
        _cleanup_signature_parts(names, kinds, has_defaults, defaults)
        return _func_type_error(cstr("unexpected native function keyword argument"))

    py_decref(remaining)
    if made_args != 0:
        py_decref(args)
    _cleanup_signature_parts(names, kinds, has_defaults, defaults)
    return bound


def _bind_signature_no_kwargs(sig, args_tuple):
    # Fast path for the dominant native-call shape: positional args only.
    # It mirrors _bind_signature but avoids constructing an empty kwargs dict
    # and reads validated tuple slots as borrowed values.
    names = _tuple_borrow_known(sig, 1)
    kinds = _tuple_borrow_known(sig, 2)
    has_defaults = _tuple_borrow_known(sig, 3)
    defaults = _tuple_borrow_known(sig, 4)
    if (
        ptr_is_null(names)
        or ptr_is_null(kinds)
        or ptr_is_null(has_defaults)
        or ptr_is_null(defaults)
    ):
        return null()

    nformals: int = load_i64(names, 16)
    if load_i64(kinds, 16) != nformals:
        return _func_type_error(cstr("invalid native function signature"))
    if load_i64(has_defaults, 16) != nformals:
        return _func_type_error(cstr("invalid native function signature"))
    if load_i64(defaults, 16) != nformals:
        return _func_type_error(cstr("invalid native function signature"))

    args = args_tuple
    made_args: int = 0
    if _is_none_or_null(args) != 0:
        args = py_tuple_new(0)
        made_args = 1
    if _is_tuple(args) == 0:
        if made_args != 0:
            py_decref(args)
        return _func_type_error(cstr("native function args must be a tuple"))

    nargs: int = load_i64(args, 16)
    if nargs == nformals:
        positional_only: int = 1
        check_i: int = 0
        while check_i < nformals:
            check_kind_obj = _tuple_borrow_known(kinds, check_i)
            if ptr_is_null(check_kind_obj):
                if made_args != 0:
                    py_decref(args)
                return null()
            check_kind: int = py_int_value_i64(check_kind_obj)
            if check_kind != 0 and check_kind != 1:
                positional_only = 0
            check_i = check_i + 1
        if positional_only != 0:
            if made_args == 0:
                py_incref(args)
            return args

    bound = py_tuple_new(nformals)
    if ptr_is_null(bound):
        if made_args != 0:
            py_decref(args)
        return null()

    pos_index: int = 0
    i: int = 0
    while i < nformals:
        kind_obj = _tuple_borrow_known(kinds, i)
        has_default_obj = _tuple_borrow_known(has_defaults, i)
        default_obj = _tuple_borrow_known(defaults, i)
        if (
            ptr_is_null(kind_obj)
            or ptr_is_null(has_default_obj)
            or ptr_is_null(default_obj)
        ):
            py_decref(bound)
            if made_args != 0:
                py_decref(args)
            return null()

        kind: int = py_int_value_i64(kind_obj)
        if kind == 3:  # PCC_FUNC_KIND_VARARGS
            varargs = _copy_varargs_known(args, pos_index, nargs)
            if ptr_is_null(varargs):
                py_decref(bound)
                if made_args != 0:
                    py_decref(args)
                return null()
            py_tuple_set_item(bound, i, varargs)
            py_decref(varargs)
            pos_index = nargs
            i = i + 1
            continue

        if kind == 4:  # PCC_FUNC_KIND_VARKW
            empty_kwargs = py_dict_new()
            if ptr_is_null(empty_kwargs):
                py_decref(bound)
                if made_args != 0:
                    py_decref(args)
                return null()
            py_tuple_set_item(bound, i, empty_kwargs)
            py_decref(empty_kwargs)
            i = i + 1
            continue

        if kind != 2 and pos_index < nargs:  # not KW_ONLY
            item = _tuple_borrow_known(args, pos_index)
            pos_index = pos_index + 1
            if ptr_is_null(item):
                py_decref(bound)
                if made_args != 0:
                    py_decref(args)
                return null()
            py_tuple_set_item(bound, i, item)
            i = i + 1
            continue

        if py_obj_truthy(has_default_obj) != 0:
            py_tuple_set_item(bound, i, default_obj)
            i = i + 1
            continue

        py_decref(bound)
        if made_args != 0:
            py_decref(args)
        return _func_type_error(cstr("missing required native function argument"))

    if pos_index < nargs:
        py_decref(bound)
        if made_args != 0:
            py_decref(args)
        return _func_type_error(
            cstr("native function got too many positional arguments")
        )

    if made_args != 0:
        py_decref(args)
    return bound


@c_abi_export("py_func_new_bound")
def py_func_new_bound(entry, captures_tuple, name, self_obj):
    if ptr_is_null(entry):
        return null()
    fn = pcc_gc_alloc(96, PY_TYPE_FUNC, 0)
    if ptr_is_null(fn):
        return null()
    store_ptr(fn, 16, null())
    store_ptr(fn, 24, null())
    store_ptr(fn, 32, null())
    store_ptr(fn, 40, null())
    store_ptr(fn, 48, null())
    store_ptr(fn, 56, entry)
    store_ptr(fn, 72, name)
    store_ptr(fn, 80, null())
    store_ptr(fn, 88, null())
    captures = captures_tuple
    made_captures: int = 0
    if ptr_is_null(captures):
        captures = py_tuple_new(0)
        made_captures = 1
    store_ptr(fn, 64, null())
    pcc_gc_store_ptr(fn, ptr_add(fn, 64), captures)
    if ptr_is_null(self_obj) == 0:
        pcc_gc_store_ptr(fn, ptr_add(fn, 80), self_obj)
    if made_captures != 0:
        py_decref(captures)
    py_gc_track(fn)
    return fn


@c_abi_export("py_func_new_named")
def py_func_new_named(entry, captures_tuple, name):
    return py_func_new_bound(entry, captures_tuple, name, null())


@c_abi_export("py_func_new")
def py_func_new(entry, captures_tuple):
    return py_func_new_named(entry, captures_tuple, null())


@c_abi_export("py_func_call_kwargs")
def py_func_call_kwargs(callable_obj, args_tuple, kwargs):
    fn = _checked_func(callable_obj)
    if ptr_is_null(fn):
        if ptr_is_null(callable_obj):
            return _func_type_error(
                cstr("native function call received NULL callable")
            )
        return _func_type_error(
            cstr("native function call requires a function object")
        )
    entry = load_ptr(fn, 56)
    if ptr_is_null(entry):
        return _func_runtime_error_if_unset(
            cstr("py_func_call_kwargs"),
            cstr("native function object has no entry point")
        )
    args = args_tuple
    made_args: int = 0
    if _is_none_or_null(args) != 0:
        args = py_tuple_new(0)
        made_args = 1
        if ptr_is_null(args):
            return _func_runtime_error_if_unset(
                cstr("py_tuple_new"),
                cstr("native function could not create its argument tuple")
            )
    captures = pcc_gc_load_ptr(fn, ptr_add(fn, 64))
    actual_captures = captures
    sig = null()
    owns_actual_captures: int = 0

    captures_len: int = 0
    if _is_tuple(captures) != 0:
        captures_len = py_tuple_len(captures)
    if captures_len == 2:
        candidate = py_tuple_get(captures, 1)
        if _signature_valid(candidate) != 0:
            inner = py_tuple_get(captures, 0)
            if ptr_is_null(inner):
                py_decref(candidate)
                if made_args != 0:
                    py_decref(args)
                return _func_runtime_error_if_unset(
                    cstr("py_func_signature_from_captures"),
                    cstr("native function signature has no captures tuple")
                )
            sig = candidate
            actual_captures = inner
            owns_actual_captures = 1
        else:
            if ptr_is_null(candidate) == 0:
                py_decref(candidate)

    kwargs_are_empty: int = _kwargs_empty(kwargs)
    if ptr_is_null(sig) and kwargs_are_empty == 0:
        if made_args != 0:
            py_decref(args)
        return _func_type_error(cstr("native function does not accept keywords"))

    call_args = args
    owns_call_args: int = 0
    if ptr_is_null(sig) == 0:
        if kwargs_are_empty != 0:
            call_args = _bind_signature_no_kwargs(sig, args)
        else:
            call_args = _bind_signature(sig, args, kwargs)
        if ptr_is_null(call_args):
            # Validate the binder's return before cleanup can run deallocators
            # and accidentally provide an unrelated pending exception.
            _func_runtime_error_if_unset(
                cstr("py_func_bind_signature"),
                cstr(
                    "native function argument binding returned NULL without exception"
                )
            )
            py_decref(sig)
            if owns_actual_captures != 0:
                py_decref(actual_captures)
            if made_args != 0:
                py_decref(args)
            return null()
        owns_call_args = 1

    result = call_ptr2(entry, actual_captures, call_args)
    # The compiled entry owns its exception contract.  Check it before
    # releasing call temporaries so cleanup cannot mask a silent NULL return.
    if ptr_is_null(result):
        entry_name = load_ptr(fn, 72)
        if ptr_is_null(entry_name):
            entry_name = cstr("<compiled native function>")
        _func_runtime_error_if_unset(
            entry_name,
            cstr("compiled native function returned NULL without exception")
        )
    if owns_call_args != 0:
        py_decref(call_args)
    if ptr_is_null(sig) == 0:
        py_decref(sig)
    if owns_actual_captures != 0:
        py_decref(actual_captures)
    if made_args != 0:
        py_decref(args)
    return result


@c_abi_export("py_func_call")
def py_func_call(callable_obj, args_tuple):
    return py_func_call_kwargs(callable_obj, args_tuple, null())


def _partial_full_args(bound, args):
    nb: int = py_tuple_len(bound)
    na: int = py_tuple_len(args)
    full = py_tuple_new(nb + na)
    if ptr_is_null(full):
        return null()
    i: int = 0
    while i < nb:
        item = py_tuple_get(bound, i)
        py_tuple_set_item(full, i, item)
        py_decref(item)
        i += 1
    j: int = 0
    while j < na:
        item = py_tuple_get(args, j)
        py_tuple_set_item(full, nb + j, item)
        py_decref(item)
        j += 1
    return full


def _pcc_partial_entry(captures, args):
    fn = py_tuple_get(captures, 0)
    bound = py_tuple_get(captures, 1)
    if ptr_is_null(fn) or ptr_is_null(bound):
        if ptr_is_null(fn) == 0:
            py_decref(fn)
        if ptr_is_null(bound) == 0:
            py_decref(bound)
        return null()
    full = _partial_full_args(bound, args)
    if ptr_is_null(full):
        py_decref(fn)
        py_decref(bound)
        return null()
    out = py_obj_call(fn, full, null())
    py_decref(full)
    py_decref(fn)
    py_decref(bound)
    return out


def _pcc_partial_kw_entry(captures, args):
    fn = py_tuple_get(captures, 0)
    bound = py_tuple_get(captures, 1)
    kwargs = py_tuple_get(captures, 2)
    if ptr_is_null(fn) or ptr_is_null(bound) or ptr_is_null(kwargs):
        if ptr_is_null(fn) == 0:
            py_decref(fn)
        if ptr_is_null(bound) == 0:
            py_decref(bound)
        if ptr_is_null(kwargs) == 0:
            py_decref(kwargs)
        return null()
    full = _partial_full_args(bound, args)
    if ptr_is_null(full):
        py_decref(fn)
        py_decref(bound)
        py_decref(kwargs)
        return null()
    out = py_obj_call(fn, full, kwargs)
    py_decref(full)
    py_decref(fn)
    py_decref(bound)
    py_decref(kwargs)
    return out


@c_abi_export("py_functools_partial")
def py_functools_partial(fn, bound_args):
    if ptr_is_null(fn):
        return null()
    bound = bound_args
    made_bound: int = 0
    if ptr_is_null(bound):
        bound = py_tuple_new(0)
        made_bound = 1
    if ptr_is_null(bound):
        return null()
    captures = py_tuple_new(2)
    if ptr_is_null(captures):
        if made_bound != 0:
            py_decref(bound)
        return null()
    py_tuple_set_item(captures, 0, fn)
    py_tuple_set_item(captures, 1, bound)
    p = py_func_new_bound_raw(_pcc_partial_entry, captures, cstr("partial"), null())
    py_decref(captures)
    if made_bound != 0:
        py_decref(bound)
    return p


@c_abi_export("py_functools_partial_kw")
def py_functools_partial_kw(fn, bound_args, bound_kwargs):
    if ptr_is_null(fn):
        return null()
    bound = bound_args
    made_bound: int = 0
    if ptr_is_null(bound):
        bound = py_tuple_new(0)
        made_bound = 1
    if ptr_is_null(bound):
        return null()
    kwargs = bound_kwargs
    made_kwargs: int = 0
    if ptr_is_null(kwargs):
        kwargs = py_dict_new()
        made_kwargs = 1
    if ptr_is_null(kwargs):
        if made_bound != 0:
            py_decref(bound)
        return null()
    captures = py_tuple_new(3)
    if ptr_is_null(captures):
        if made_bound != 0:
            py_decref(bound)
        if made_kwargs != 0:
            py_decref(kwargs)
        return null()
    py_tuple_set_item(captures, 0, fn)
    py_tuple_set_item(captures, 1, bound)
    py_tuple_set_item(captures, 2, kwargs)
    p = py_func_new_bound_raw(
        _pcc_partial_kw_entry,
        captures,
        cstr("partial"),
        null(),
    )
    py_decref(captures)
    if made_bound != 0:
        py_decref(bound)
    if made_kwargs != 0:
        py_decref(kwargs)
    return p


def _update_wrapper_copy_attr(wrapper, wrapped, name) -> int:
    value = py_obj_getattr(wrapped, name)
    if ptr_is_null(value):
        py_clear_exception()
        return 0
    status: int = py_obj_setattr(wrapper, name, value)
    py_decref(value)
    return status


@c_abi_export("py_functools_update_wrapper")
def py_functools_update_wrapper(wrapper, wrapped):
    if ptr_is_null(wrapper) or ptr_is_null(wrapped):
        return null()
    if _update_wrapper_copy_attr(wrapper, wrapped, cstr("__module__")) != 0:
        return null()
    if _update_wrapper_copy_attr(wrapper, wrapped, cstr("__name__")) != 0:
        return null()
    if _update_wrapper_copy_attr(wrapper, wrapped, cstr("__qualname__")) != 0:
        return null()
    if _update_wrapper_copy_attr(wrapper, wrapped, cstr("__doc__")) != 0:
        return null()
    if _update_wrapper_copy_attr(wrapper, wrapped, cstr("__annotations__")) != 0:
        return null()
    if _update_wrapper_copy_attr(wrapper, wrapped, cstr("__type_params__")) != 0:
        return null()

    wrapper_dict = py_obj_getattr(wrapper, cstr("__dict__"))
    if ptr_is_null(wrapper_dict):
        py_clear_exception()
    wrapped_dict = py_obj_getattr(wrapped, cstr("__dict__"))
    if ptr_is_null(wrapped_dict):
        py_clear_exception()
    if ptr_is_null(wrapper_dict) == 0 and ptr_is_null(wrapped_dict) == 0:
        py_dict_update(wrapper_dict, wrapped_dict)
    py_decref(wrapper_dict)
    py_decref(wrapped_dict)

    if py_obj_setattr(wrapper, cstr("__wrapped__"), wrapped) != 0:
        return null()
    py_incref(wrapper)
    return wrapper


@c_abi_export("py_dealloc_func")
def py_dealloc_func(o) -> None:
    capi_self = pcc_gc_load_ptr(o, ptr_add(o, 24))
    capi_module = pcc_gc_load_ptr(o, ptr_add(o, 32))
    capi_weakreflist = pcc_gc_load_ptr(o, ptr_add(o, 40))
    captures = pcc_gc_load_ptr(o, ptr_add(o, 64))
    self_obj = pcc_gc_load_ptr(o, ptr_add(o, 80))
    attrs = pcc_gc_load_ptr(o, ptr_add(o, 88))
    if ptr_is_null(capi_self) == 0:
        py_decref(capi_self)
    if ptr_is_null(capi_module) == 0:
        py_decref(capi_module)
    if ptr_is_null(capi_weakreflist) == 0:
        py_decref(capi_weakreflist)
    py_decref(captures)
    if ptr_is_null(self_obj) == 0:
        py_decref(self_obj)
    if ptr_is_null(attrs) == 0:
        py_decref(attrs)
    pcc_gc_free_object_memory(o)
