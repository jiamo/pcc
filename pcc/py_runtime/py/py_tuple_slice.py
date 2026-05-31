"""Tuple slice split out from py_tuple.

Keep ``py_tuple_slice`` in its own archive member so ordinary tuple
construction, indexing, len, concat, and compare paths do not force tuple
slicing into user-runtime executables.
"""

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr,
    getenv,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    malloc,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    untag_int,
)


py_obj_index_i64 = extern("py_obj_index_i64", (c_ptr,), c_int64)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
_pcc_debug_bad_incref = extern("pcc_debug_bad_incref", (c_ptr, c_int32), c_void)


def _debug_bad_container(o, code: int) -> None:
    if ptr_is_null(getenv(cstr("PCC_DEBUG_RUNTIME"))) == 0:
        _pcc_debug_bad_incref(o, code)


def _ptr_can_have_header(o) -> bool:
    if ptr_is_null(o) != 0:
        return False
    if is_tagged_int(o) != 0:
        return False
    bits: int = untag_int(o)
    if bits < 2048:
        return False
    if (bits & 3) != 0:
        return False
    if bits >= 140737488355328:
        return False
    return True


def _ptr_is_tuple(o) -> bool:
    if not _ptr_can_have_header(o):
        return False
    return load_i32(o, 8) == 7


def _tuple_is_sane(tuple_ptr, code: int) -> bool:
    if ptr_is_null(tuple_ptr) != 0:
        return False
    if not _ptr_is_tuple(tuple_ptr):
        _debug_bad_container(tuple_ptr, code)
        return False
    length: int = load_i64(tuple_ptr, 16)
    if length < 0:
        _debug_bad_container(tuple_ptr, code)
        return False
    if length > 134217728:
        _debug_bad_container(tuple_ptr, code)
        return False
    return True


def _is_none_or_null(o) -> int:
    if ptr_is_null(o):
        return 1
    if ptr_eq(o, global_load_ptr("py_None")):
        return 1
    return 0


def _slice_count(lo_v: int, hi_v: int, step_v: int) -> int:
    count: int = 0
    if step_v > 0:
        i: int = lo_v
        while i < hi_v:
            count = count + 1
            i = i + step_v
    else:
        i: int = lo_v
        while i > hi_v:
            count = count + 1
            i = i + step_v
    return count


@c_abi_export("py_tuple_slice")
def py_tuple_slice(tuple_ptr, lo, hi, step):
    if not _tuple_is_sane(tuple_ptr, -134):
        return null()
    length: int = load_i64(tuple_ptr, 16)

    step_v: int = 1
    if _is_none_or_null(step) == 0:
        step_v = py_obj_index_i64(step)
        if py_err_occurred() != 0:
            return null()
        if step_v == 0:
            return null()

    lo_v: int = 0
    hi_v: int = length
    if step_v > 0:
        if _is_none_or_null(lo) != 0:
            lo_v = 0
        else:
            lo_v = py_obj_index_i64(lo)
            if py_err_occurred() != 0:
                return null()
        if _is_none_or_null(hi) != 0:
            hi_v = length
        else:
            hi_v = py_obj_index_i64(hi)
            if py_err_occurred() != 0:
                return null()
    else:
        if _is_none_or_null(lo) != 0:
            lo_v = length - 1
        else:
            lo_v = py_obj_index_i64(lo)
            if py_err_occurred() != 0:
                return null()
        if _is_none_or_null(hi) != 0:
            hi_v = -1
        else:
            hi_v = py_obj_index_i64(hi)
            if py_err_occurred() != 0:
                return null()

    if step_v > 0:
        if lo_v < 0:
            lo_v = lo_v + length
            if lo_v < 0:
                lo_v = 0
        if lo_v > length:
            lo_v = length
        if hi_v < 0:
            hi_v = hi_v + length
            if hi_v < 0:
                hi_v = 0
        if hi_v > length:
            hi_v = length
    else:
        if lo_v < 0:
            lo_v = lo_v + length
            if lo_v < 0:
                lo_v = -1
        if lo_v >= length:
            lo_v = length - 1
        if hi_v < 0:
            if _is_none_or_null(hi) != 0:
                hi_v = -1
            else:
                hi_v = hi_v + length
                if hi_v < 0:
                    hi_v = -1
        if hi_v >= length:
            hi_v = length - 1

    out = py_tuple_new(_slice_count(lo_v, hi_v, step_v))
    if ptr_is_null(out):
        return null()
    j: int = 0
    if step_v > 0:
        i: int = lo_v
        while i < hi_v:
            v = pcc_gc_load_ptr(tuple_ptr, ptr_add(tuple_ptr, 24 + i * 8))
            py_tuple_set_item(out, j, v)
            j = j + 1
            i = i + step_v
    else:
        i: int = lo_v
        while i > hi_v:
            if i < 0 or i >= length:
                hi_v = i
            else:
                v = pcc_gc_load_ptr(tuple_ptr, ptr_add(tuple_ptr, 24 + i * 8))
                py_tuple_set_item(out, j, v)
                j = j + 1
                i = i + step_v
    return out
