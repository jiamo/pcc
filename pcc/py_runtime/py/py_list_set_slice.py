"""List set-slice support split out from py_list.

The common list object member is pulled into most executables for
``py_list_new``/``py_list_get``/``py_list_append``. Keep set-slice in its own
archive member so that ordinary list users do not pay for it.
"""
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_INT,
    PY_TYPE_LIST,
    PY_TYPE_TUPLE,
)

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    memmove,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    ptr_to_int,
    realloc,
    store_i64,
    store_ptr,
    untag_int,
)


py_decref = extern("py_decref", (c_ptr,), c_void)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
_pcc_debug_bad_incref = extern("pcc_debug_bad_incref", (c_ptr, c_int32), c_void)
getenv = extern("pcc_platform_getenv", (c_ptr,), c_ptr)


def _debug_bad_container(o, code: int) -> None:
    if ptr_is_null(getenv(cstr("PCC_DEBUG_RUNTIME"))) == 0:
        _pcc_debug_bad_incref(o, code)


pcc_gc_pointer_is_managed = extern(
    "pcc_gc_pointer_is_managed", (c_ptr,), c_int64
)


def _ptr_can_have_header(o) -> bool:
    return pcc_gc_pointer_is_managed(o) != 0


def _ptr_is_list(o) -> bool:
    if not _ptr_can_have_header(o):
        return False
    return load_i32(o, 8) == PY_TYPE_LIST


def _list_is_sane(lst, code: int) -> bool:
    if ptr_is_null(lst) != 0:
        return False
    if not _ptr_is_list(lst):
        _debug_bad_container(lst, code)
        return False
    length: int = load_i64(lst, 16)
    capacity: int = load_i64(lst, 24)
    items = load_ptr(lst, 32)
    if length < 0:
        _debug_bad_container(lst, code)
        return False
    if capacity < length:
        _debug_bad_container(lst, code)
        return False
    if capacity > 134217728:
        _debug_bad_container(lst, code)
        return False
    if ptr_is_null(items) != 0:
        _debug_bad_container(lst, code)
        return False
    return True


def _type_of(obj) -> int:
    if not _ptr_can_have_header(obj):
        if is_tagged_int(obj):
            return PY_TYPE_INT
        return -1
    return load_i32(obj, 8)


def _is_none_or_null(o) -> int:
    if ptr_is_null(o):
        return 1
    if ptr_eq(o, global_load_ptr("py_None")):
        return 1
    return 0


def _grow_if_needed(l, want: int) -> int:
    if not _list_is_sane(l, -102):
        return -1
    capacity: int = load_i64(l, 24)
    if capacity >= want:
        return 0
    cap: int = capacity
    if cap <= 0:
        cap = 4
    while cap < want:
        cap = cap * 2
    items = load_ptr(l, 32)
    new_items = realloc(items, cap * 8)
    if ptr_is_null(new_items):
        return -1
    store_ptr(l, 32, new_items)
    store_i64(l, 24, cap)
    return 0


def _seq_len(seq) -> int:
    if ptr_is_null(seq):
        return -1
    tag: int = _type_of(seq)
    if tag == PY_TYPE_LIST:
        if not _list_is_sane(seq, -114):
            return -1
        return load_i64(seq, 16)
    if tag == PY_TYPE_TUPLE:
        n: int = load_i64(seq, 16)
        if n < 0 or n > 134217728:
            _debug_bad_container(seq, -115)
            return -1
        return n
    return -1


def _seq_get_borrowed(seq, i: int):
    tag: int = _type_of(seq)
    if tag == PY_TYPE_LIST:
        if not _list_is_sane(seq, -116):
            return null()
        items = load_ptr(seq, 32)
        return pcc_gc_load_ptr(seq, ptr_add(items, i * 8))
    if tag == PY_TYPE_TUPLE:
        items = ptr_add(seq, 24)
        return pcc_gc_load_ptr(seq, ptr_add(items, i * 8))
    return null()


def _slice_count(lo: int, hi: int, step: int) -> int:
    n: int = 0
    if step > 0:
        i: int = lo
        while i < hi:
            n = n + 1
            i = i + step
    else:
        i: int = lo
        while i > hi:
            if i < 0:
                return n
            n = n + 1
            i = i + step
    return n


@c_abi_export("py_list_set_slice")
def py_list_set_slice(lst, lo, hi, step, replacement) -> int:
    if not _list_is_sane(lst, -111):
        return -1
    if ptr_is_null(replacement):
        return -1
    length: int = load_i64(lst, 16)

    step_v: int = 1
    if _is_none_or_null(step) == 0:
        step_v = py_int_value_i64(step)
        if step_v == 0:
            return -1

    lo_v: int = 0
    hi_v: int = length
    if step_v > 0:
        if _is_none_or_null(lo) != 0:
            lo_v = 0
        else:
            lo_v = py_int_value_i64(lo)
        if _is_none_or_null(hi) != 0:
            hi_v = length
        else:
            hi_v = py_int_value_i64(hi)
    else:
        if _is_none_or_null(lo) != 0:
            lo_v = length - 1
        else:
            lo_v = py_int_value_i64(lo)
        if _is_none_or_null(hi) != 0:
            hi_v = -1
        else:
            hi_v = py_int_value_i64(hi)

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

    repl_len: int = _seq_len(replacement)
    if repl_len < 0:
        return -1

    if step_v == 1:
        range_hi: int = hi_v
        if range_hi < lo_v:
            range_hi = lo_v
        remove_len: int = 0
        if range_hi > lo_v:
            remove_len = range_hi - lo_v
        new_len: int = length - remove_len + repl_len
        if _grow_if_needed(lst, new_len) != 0:
            return -1
        items = load_ptr(lst, 32)
        i: int = lo_v
        while i < lo_v + remove_len:
            slot = ptr_add(items, i * 8)
            old = pcc_gc_load_ptr(lst, slot)
            store_ptr(items, i * 8, null())
            if ptr_is_null(old) == 0:
                py_decref(old)
            i = i + 1
        if repl_len != remove_len and range_hi < length:
            src = ptr_add(items, range_hi * 8)
            dst = ptr_add(items, (lo_v + repl_len) * 8)
            memmove(dst, src, (length - range_hi) * 8)
        j: int = 0
        while j < repl_len:
            v = _seq_get_borrowed(replacement, j)
            store_ptr(items, (lo_v + j) * 8, null())
            pcc_gc_store_ptr(lst, ptr_add(items, (lo_v + j) * 8), v)
            j = j + 1
        store_i64(lst, 16, new_len)
        return 0

    expected: int = _slice_count(lo_v, hi_v, step_v)
    if repl_len != expected:
        return -1
    items = load_ptr(lst, 32)
    idx: int = lo_v
    k: int = 0
    while k < repl_len:
        if idx < 0 or idx >= length:
            return -1
        v = _seq_get_borrowed(replacement, k)
        old = pcc_gc_load_ptr(lst, ptr_add(items, idx * 8))
        store_ptr(items, idx * 8, null())
        if ptr_is_null(old) == 0:
            py_decref(old)
        pcc_gc_store_ptr(lst, ptr_add(items, idx * 8), v)
        idx = idx + step_v
        k = k + 1
    return 0
