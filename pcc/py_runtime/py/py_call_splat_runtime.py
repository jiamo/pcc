"""pcc-Python helpers for ``*args``, ``**kwargs``, and ``zip(*rows)``."""
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_DICT,
    PY_TYPE_INT,
    PY_TYPE_LIST,
    PY_TYPE_TUPLE,
)

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import cstr, global_load_ptr, is_tagged_int, load_i32, null, ptr_eq, ptr_is_null


py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_update = extern("py_dict_update", (c_ptr, c_ptr), c_void)
py_obj_len = extern("py_obj_len", (c_ptr,), c_int64)
py_obj_getitem = extern("py_obj_getitem", (c_ptr, c_ptr), c_ptr)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_runtime_error_if_unset = extern(
    "py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr
)


def _require_result(result, helper_name, message):
    if ptr_is_null(result):
        py_runtime_error_if_unset(helper_name, message)
    return result


def _is_none(o) -> bool:
    return ptr_is_null(o) or ptr_eq(o, global_load_ptr("py_None"))


def _type_of(o) -> int:
    if ptr_is_null(o):
        return -1
    if is_tagged_int(o):
        return PY_TYPE_INT
    return load_i32(o, 8)


def _sequence_len(o) -> int:
    tag: int = _type_of(o)
    if tag == PY_TYPE_TUPLE:
        return py_tuple_len(o)
    if tag == PY_TYPE_LIST:
        return py_list_len(o)
    return -1


def _sequence_get(o, index: int):
    if _type_of(o) == PY_TYPE_TUPLE:
        return py_tuple_get(o, index)
    if _type_of(o) == PY_TYPE_LIST:
        return py_list_get(o, index)
    return null()


@c_abi_export("py_call_merge_posargs")
def py_call_merge_posargs(base_tuple, star_args):
    if _is_none(base_tuple):
        base_tuple = py_tuple_new(0)
        if ptr_is_null(base_tuple):
            return _require_result(
                null(),
                cstr("py_tuple_new"),
                cstr("call splat could not allocate the base argument tuple"),
            )
    elif _type_of(base_tuple) != PY_TYPE_TUPLE:
        py_raise(py_exc_new(3, cstr("call args base must be tuple")))
        return null()
    else:
        py_incref(base_tuple)

    base_len: int = py_tuple_len(base_tuple)
    if _is_none(star_args):
        return base_tuple
    star_len: int = _sequence_len(star_args)
    if star_len < 0:
        py_decref(base_tuple)
        py_raise(py_exc_new(3, cstr("*args must be tuple or list")))
        return null()

    out = py_tuple_new(base_len + star_len)
    if ptr_is_null(out):
        _require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("call splat could not allocate the merged argument tuple"),
        )
        py_decref(base_tuple)
        return null()
    i: int = 0
    while i < base_len:
        item = py_tuple_get(base_tuple, i)
        if ptr_is_null(item):
            _require_result(
                null(),
                cstr("py_tuple_get"),
                cstr("call splat could not read a base positional argument"),
            )
            py_decref(out)
            py_decref(base_tuple)
            return null()
        py_tuple_set_item(out, i, item)
        py_decref(item)
        i = i + 1
    i = 0
    while i < star_len:
        item = _sequence_get(star_args, i)
        if ptr_is_null(item):
            _require_result(
                null(),
                cstr("pcc_sequence_get_for_splat"),
                cstr("call splat could not read a starred positional argument"),
            )
            py_decref(out)
            py_decref(base_tuple)
            return null()
        py_tuple_set_item(out, base_len + i, item)
        py_decref(item)
        i = i + 1
    py_decref(base_tuple)
    return out


@c_abi_export("py_zip_star")
def py_zip_star(rows):
    if _is_none(rows):
        return _require_result(
            py_list_new(0),
            cstr("py_list_new"),
            cstr("zip splat could not allocate its result list"),
        )
    nrows: int = _sequence_len(rows)
    if nrows < 0:
        py_raise(py_exc_new(3, cstr("zip(*x): x must be a tuple or list")))
        return null()
    if nrows == 0:
        return _require_result(
            py_list_new(0),
            cstr("py_list_new"),
            cstr("zip splat could not allocate its empty result list"),
        )

    min_len: int = -1
    row_index: int = 0
    while row_index < nrows:
        row = _sequence_get(rows, row_index)
        if ptr_is_null(row):
            return _require_result(
                null(),
                cstr("pcc_sequence_get_for_splat"),
                cstr("zip splat could not read an input row"),
            )
        row_len: int = py_obj_len(row)
        if row_len < 0:
            _require_result(
                null(),
                cstr("py_obj_len"),
                cstr("zip splat row length failed without setting an exception"),
            )
            py_decref(row)
            return null()
        py_decref(row)
        if min_len < 0 or row_len < min_len:
            min_len = row_len
        row_index = row_index + 1
    if min_len < 0:
        min_len = 0

    out = py_list_new(0)
    if ptr_is_null(out):
        return _require_result(
            null(),
            cstr("py_list_new"),
            cstr("zip splat could not allocate its result list"),
        )
    column: int = 0
    while column < min_len:
        column_obj = py_int_from_i64(column)
        if ptr_is_null(column_obj):
            _require_result(
                null(),
                cstr("py_int_from_i64"),
                cstr("zip splat could not allocate a column index"),
            )
            py_decref(out)
            return null()
        tuple_obj = py_tuple_new(nrows)
        if ptr_is_null(tuple_obj):
            _require_result(
                null(),
                cstr("py_tuple_new"),
                cstr("zip splat could not allocate a result row"),
            )
            py_decref(column_obj)
            py_decref(out)
            return null()
        row_index = 0
        while row_index < nrows:
            row = _sequence_get(rows, row_index)
            if ptr_is_null(row):
                _require_result(
                    null(),
                    cstr("pcc_sequence_get_for_splat"),
                    cstr("zip splat could not reload an input row"),
                )
                py_decref(tuple_obj)
                py_decref(column_obj)
                py_decref(out)
                return null()
            element = py_obj_getitem(row, column_obj)
            if ptr_is_null(element):
                _require_result(
                    null(),
                    cstr("py_obj_getitem"),
                    cstr("zip splat element lookup failed without setting an exception"),
                )
                py_decref(row)
                py_decref(tuple_obj)
                py_decref(column_obj)
                py_decref(out)
                return null()
            py_decref(row)
            py_tuple_set_item(tuple_obj, row_index, element)
            py_decref(element)
            row_index = row_index + 1
        py_decref(column_obj)
        py_list_append(out, tuple_obj)
        py_decref(tuple_obj)
        column = column + 1
    return out


def _dict_clone(source):
    out = py_dict_new()
    if ptr_is_null(out):
        return _require_result(
            null(),
            cstr("py_dict_new"),
            cstr("call splat could not allocate the merged keyword dictionary"),
        )
    if not _is_none(source):
        if _type_of(source) != PY_TYPE_DICT:
            py_decref(out)
            py_raise(py_exc_new(3, cstr("kwargs base must be dict")))
            return null()
        py_dict_update(out, source)
    return out


@c_abi_export("py_call_merge_kwargs")
def py_call_merge_kwargs(base_kwargs, star_kwargs):
    out = _dict_clone(base_kwargs)
    if ptr_is_null(out):
        return _require_result(
            null(),
            cstr("pcc_dict_clone"),
            cstr("call splat could not clone its keyword dictionary"),
        )
    if _is_none(star_kwargs):
        return out
    if _type_of(star_kwargs) != PY_TYPE_DICT:
        py_decref(out)
        py_raise(py_exc_new(3, cstr("**kwargs must be dict")))
        return null()
    py_dict_update(out, star_kwargs)
    return out


@c_abi_export("py_obj_call_splat")
def py_obj_call_splat(callable_obj, base_args, star_args, base_kwargs, star_kwargs):
    args = py_call_merge_posargs(base_args, star_args)
    if ptr_is_null(args):
        return _require_result(
            null(),
            cstr("py_call_merge_posargs"),
            cstr("call splat could not merge positional arguments"),
        )
    kwargs = py_call_merge_kwargs(base_kwargs, star_kwargs)
    if ptr_is_null(kwargs):
        _require_result(
            null(),
            cstr("py_call_merge_kwargs"),
            cstr("call splat could not merge keyword arguments"),
        )
        py_decref(args)
        return null()
    out = py_obj_call(callable_obj, args, kwargs)
    if ptr_is_null(out):
        _require_result(
            null(),
            cstr("py_obj_call"),
            cstr("call splat callee returned NULL without setting an exception"),
        )
    py_decref(args)
    py_decref(kwargs)
    return out
