"""Phase 4c.13: pcc-Python port of py_list.c.

Growable PyObject* array with owned references.

PyListObject layout (from py_internal.h):
    offset  0   PyObjectHeader   (16 bytes)
    offset 16   length           (i64)
    offset 24   capacity         (i64)
    offset 32   items            (PyObject** — pointer to ptr array)
    total: 40 bytes

PyTupleObject layout:
    offset  0   PyObjectHeader   (16 bytes)
    offset 16   len              (i64)
    offset 24   items[]          (flexible-array-member of PyObject*)

Constants (inlined per the module-init gotcha):
    PY_TYPE_LIST  = 5
    PY_TYPE_TUPLE = 7
    PY_TYPE_EXC   = 12
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
    free,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memmove,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    realloc,
    store_i32,
    store_i64,
    store_ptr,
)

py_incref            = extern("py_incref",            (c_ptr,),                    c_void)
py_decref            = extern("py_decref",            (c_ptr,),                    c_void)
py_obj_eq            = extern("py_obj_eq",            (c_ptr, c_ptr),              c_int32)
py_int_value_i64     = extern("py_int_value_i64",     (c_ptr,),                    c_int64)
py_exc_new           = extern("py_exc_new",           (c_int64, c_ptr),            c_ptr)
py_raise             = extern("py_raise",             (c_ptr,),                    c_void)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)


def _is_none_or_null(o) -> int:
    if ptr_is_null(o):
        return 1
    if ptr_eq(o, global_load_ptr("py_None")):
        return 1
    return 0


def _grow_if_needed(l, want: int) -> int:
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


def _normalize_index(i: int, length: int, clip: int) -> int:
    if clip != 0:
        if i < 0:
            i = i + length
            if i < 0:
                i = 0
        if i > length:
            i = length
        return i
    if i < 0:
        i = i + length
    if i < 0 or i >= length:
        return -1
    return i


@c_abi_export("py_list_new")
def py_list_new(initial_capacity: int):
    l = malloc(40)
    if ptr_is_null(l):
        return null()
    store_i64(l, 0, 1)              # refcount
    store_i32(l, 8, 5)              # PY_TYPE_LIST
    store_i32(l, 12, 0)             # flags
    store_i64(l, 16, 0)             # length
    cap: int = initial_capacity
    if cap < 4:
        cap = 4
    store_i64(l, 24, cap)
    items = malloc(cap * 8)
    if ptr_is_null(items):
        free(l)
        return null()
    store_ptr(l, 32, items)
    return l


@c_abi_export("py_list_append")
def py_list_append(lst, item) -> None:
    if ptr_is_null(lst):
        return
    length: int = load_i64(lst, 16)
    if _grow_if_needed(lst, length + 1) != 0:
        return
    items = load_ptr(lst, 32)
    py_incref(item)
    store_ptr(items, length * 8, item)
    store_i64(lst, 16, length + 1)


@c_abi_export("py_list_get")
def py_list_get(lst, i: int):
    if ptr_is_null(lst):
        return null()
    length: int = load_i64(lst, 16)
    idx: int = _normalize_index(i, length, 0)
    if idx < 0:
        return null()
    items = load_ptr(lst, 32)
    v = load_ptr(items, idx * 8)
    py_incref(v)
    return v


@c_abi_export("py_list_set")
def py_list_set(lst, i: int, item) -> None:
    if ptr_is_null(lst):
        return
    length: int = load_i64(lst, 16)
    idx: int = _normalize_index(i, length, 0)
    if idx < 0:
        return
    items = load_ptr(lst, 32)
    py_incref(item)
    old = load_ptr(items, idx * 8)
    py_decref(old)
    store_ptr(items, idx * 8, item)


@c_abi_export("py_list_len")
def py_list_len(lst) -> int:
    if ptr_is_null(lst):
        return 0
    return load_i64(lst, 16)


@c_abi_export("py_list_concat")
def py_list_concat(a, b):
    if ptr_is_null(a):
        return null()
    if ptr_is_null(b):
        return null()
    la: int = load_i64(a, 16)
    lb: int = load_i64(b, 16)
    n: int = la + lb
    cap_hint: int = n
    if cap_hint <= 0:
        cap_hint = 4
    out = py_list_new(cap_hint)
    if ptr_is_null(out):
        return null()
    out_items = load_ptr(out, 32)
    a_items = load_ptr(a, 32)
    i: int = 0
    while i < la:
        v = load_ptr(a_items, i * 8)
        py_incref(v)
        store_ptr(out_items, i * 8, v)
        i = i + 1
    b_items = load_ptr(b, 32)
    j: int = 0
    while j < lb:
        v = load_ptr(b_items, j * 8)
        py_incref(v)
        store_ptr(out_items, (la + j) * 8, v)
        j = j + 1
    store_i64(out, 16, n)
    return out


@c_abi_export("py_list_repeat")
def py_list_repeat(src, count: int):
    if ptr_is_null(src):
        return null()
    sl: int = load_i64(src, 16)
    real_count: int = count
    if real_count < 0:
        real_count = 0
    out_len: int = sl * real_count
    cap_hint: int = out_len
    if cap_hint <= 0:
        cap_hint = 4
    out = py_list_new(cap_hint)
    if ptr_is_null(out):
        return null()
    out_items = load_ptr(out, 32)
    src_items = load_ptr(src, 32)
    pos: int = 0
    k: int = 0
    while k < real_count:
        i: int = 0
        while i < sl:
            v = load_ptr(src_items, i * 8)
            py_incref(v)
            store_ptr(out_items, pos * 8, v)
            pos = pos + 1
            i = i + 1
        k = k + 1
    store_i64(out, 16, out_len)
    return out


@c_abi_export("py_list_contains")
def py_list_contains(lst, item) -> int:
    if ptr_is_null(lst):
        return 0
    length: int = load_i64(lst, 16)
    items = load_ptr(lst, 32)
    i: int = 0
    while i < length:
        v = load_ptr(items, i * 8)
        if py_obj_eq(v, item) != 0:
            return 1
        i = i + 1
    return 0


def _push_to_list(out, v) -> int:
    # Helper: append v to out with grow-check; return -1 on alloc fail.
    out_len: int = load_i64(out, 16)
    if _grow_if_needed(out, out_len + 1) != 0:
        return -1
    out_items = load_ptr(out, 32)
    py_incref(v)
    store_ptr(out_items, out_len * 8, v)
    store_i64(out, 16, out_len + 1)
    return 0


@c_abi_export("py_list_slice")
def py_list_slice(lst, lo, hi, step):
    if ptr_is_null(lst):
        return null()
    length: int = load_i64(lst, 16)

    step_v: int = 1
    if _is_none_or_null(step) == 0:
        step_v = py_int_value_i64(step)
        if step_v == 0:
            return null()

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

    out = py_list_new(4)
    if ptr_is_null(out):
        return null()
    items = load_ptr(lst, 32)

    if step_v > 0:
        i: int = lo_v
        while i < hi_v:
            v = load_ptr(items, i * 8)
            if _push_to_list(out, v) != 0:
                py_decref(out)
                return null()
            i = i + step_v
    else:
        i: int = lo_v
        while i > hi_v:
            if i < 0 or i >= length:
                # break loop
                hi_v = i        # force exit
            else:
                v = load_ptr(items, i * 8)
                if _push_to_list(out, v) != 0:
                    py_decref(out)
                    return null()
                i = i + step_v
    return out


@c_abi_export("py_list_extend")
def py_list_extend(a, b) -> None:
    if ptr_is_null(a):
        return
    if ptr_is_null(b):
        return
    btag: int = _type_of(b)
    if btag == 5:                          # PY_TYPE_LIST
        bl: int = load_i64(b, 16)
        la: int = load_i64(a, 16)
        if _grow_if_needed(a, la + bl) != 0:
            return
        a_items = load_ptr(a, 32)
        b_items = load_ptr(b, 32)
        i: int = 0
        while i < bl:
            v = load_ptr(b_items, i * 8)
            py_incref(v)
            store_ptr(a_items, (la + i) * 8, v)
            i = i + 1
        store_i64(a, 16, la + bl)
        return
    if btag == 7:                          # PY_TYPE_TUPLE
        # PyTupleObject layout: header(16) + len(16) + items(24, flex array)
        bl: int = load_i64(b, 16)
        la: int = load_i64(a, 16)
        if _grow_if_needed(a, la + bl) != 0:
            return
        a_items = load_ptr(a, 32)
        b_items_base = ptr_add(b, 24)        # &items[0]
        i: int = 0
        while i < bl:
            v = load_ptr(b_items_base, i * 8)
            py_incref(v)
            store_ptr(a_items, (la + i) * 8, v)
            i = i + 1
        store_i64(a, 16, la + bl)
        return
    # TODO(phase2): generic iterable protocol


@c_abi_export("py_list_insert")
def py_list_insert(lst, i: int, item) -> None:
    if ptr_is_null(lst):
        return
    length: int = load_i64(lst, 16)
    idx: int = _normalize_index(i, length, 1)
    if _grow_if_needed(lst, length + 1) != 0:
        return
    items = load_ptr(lst, 32)
    if idx < length:
        # Shift tail [idx, length) right by one.
        src = ptr_add(items, idx * 8)
        dst = ptr_add(items, (idx + 1) * 8)
        memmove(dst, src, (length - idx) * 8)
    py_incref(item)
    store_ptr(items, idx * 8, item)
    store_i64(lst, 16, length + 1)


@c_abi_export("py_list_pop")
def py_list_pop(lst, i: int):
    if ptr_is_null(lst):
        return null()
    length: int = load_i64(lst, 16)
    if length == 0:
        return null()
    idx: int = 0
    if i == -1:
        idx = length - 1
    else:
        idx = _normalize_index(i, length, 0)
        if idx < 0:
            return null()
    items = load_ptr(lst, 32)
    v = load_ptr(items, idx * 8)
    if idx < length - 1:
        src = ptr_add(items, (idx + 1) * 8)
        dst = ptr_add(items, idx * 8)
        memmove(dst, src, (length - idx - 1) * 8)
    store_i64(lst, 16, length - 1)
    return v


@c_abi_export("py_list_remove")
def py_list_remove(lst, item) -> None:
    if ptr_is_null(lst):
        return
    length: int = load_i64(lst, 16)
    items = load_ptr(lst, 32)
    i: int = 0
    while i < length:
        v = load_ptr(items, i * 8)
        if py_obj_eq(v, item) != 0:
            py_decref(v)
            if i < length - 1:
                src = ptr_add(items, (i + 1) * 8)
                dst = ptr_add(items, i * 8)
                memmove(dst, src, (length - i - 1) * 8)
            store_i64(lst, 16, length - 1)
            return
        i = i + 1
    # Not found: raise ValueError. py_exc_new takes int64 tag.
    # PY_TYPE_EXC = 12 — match the C version's PY_TYPE_EXC arg.
    exc = py_exc_new(12, null())
    py_raise(exc)
    if not ptr_is_null(exc):
        py_decref(exc)


@c_abi_export("py_list_index")
def py_list_index(lst, item) -> int:
    if ptr_is_null(lst):
        return -1
    length: int = load_i64(lst, 16)
    items = load_ptr(lst, 32)
    i: int = 0
    while i < length:
        v = load_ptr(items, i * 8)
        if py_obj_eq(v, item) != 0:
            return i
        i = i + 1
    return -1
