"""Phase 4c.1: pcc-Python replacement for py_runtime/src/py_tuple.c.

Each public entry point is decorated with ``@c_abi_export(<name>)`` so
pcc emits the unmangled C-ABI symbol name directly. This lets the
.o produced from this module drop-in replace py_tuple.o at link time.

Layout mirrors py_tuple.c exactly:

    typedef struct {
        PyObjectHeader h;          // 16 bytes (i64 refcount, i32 tag, i32 flags)
        int64_t        len;        // 8 bytes
        PyObject      *items[];    // flexible array of owned refs
    } PyTupleObject;

Offsets: 0 refcount, 8 type_tag, 12 flags, 16 len, 24 items[0...]

Layout constants are inlined as integer literals in each function to
avoid pcc's module-level-constant lowering (which requires a main()
init function that conflicts with library linkage).
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.unsafe import (
    global_load_ptr,
    load_i64,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


py_incref         = extern("py_incref",        (c_ptr,),                             c_void)
py_int_value_i64  = extern("py_int_value_i64", (c_ptr,),                             c_int64)


def _is_none_or_null(o) -> int:
    if ptr_is_null(o):
        return 1
    if ptr_eq(o, global_load_ptr("py_None")):
        return 1
    return 0


@c_abi_export("py_tuple_new")
def py_tuple_new(n: int):
    if n < 0:
        n = 0
    bytes_total: int = 24 + n * 8          # OFFSET_ITEMS + n * SIZEOF_PTR
    t = malloc(bytes_total)
    store_i64(t, 0, 1)              # refcount = 1
    store_i32(t, 8, 7)              # type_tag = PY_TYPE_TUPLE
    store_i32(t, 12, 0)             # flags = 0
    store_i64(t, 16, n)             # len = n
    if n > 0:
        items_ptr = ptr_add(t, 24)
        memset(items_ptr, 0, n * 8)
    return t


@c_abi_export("py_tuple_set_item")
def py_tuple_set_item(tuple_ptr, i: int, item) -> None:
    if tuple_ptr is None:
        return
    tuple_len: int = load_i64(tuple_ptr, 16)
    if i < 0 or i >= tuple_len:
        return
    py_incref(item)
    slot_offset: int = 24 + i * 8
    store_ptr(tuple_ptr, slot_offset, item)


@c_abi_export("py_tuple_get")
def py_tuple_get(tuple_ptr, i: int):
    if tuple_ptr is None:
        return None
    tuple_len: int = load_i64(tuple_ptr, 16)
    if i < 0:
        i = i + tuple_len
    if i < 0 or i >= tuple_len:
        return None
    slot_offset: int = 24 + i * 8
    v = load_ptr(tuple_ptr, slot_offset)
    py_incref(v)
    return v


@c_abi_export("py_tuple_len")
def py_tuple_len(tuple_ptr) -> int:
    if tuple_ptr is None:
        return 0
    return load_i64(tuple_ptr, 16)


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
    if ptr_is_null(tuple_ptr):
        return null()
    length: int = load_i64(tuple_ptr, 16)

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

    out = py_tuple_new(_slice_count(lo_v, hi_v, step_v))
    if ptr_is_null(out):
        return null()
    j: int = 0
    if step_v > 0:
        i: int = lo_v
        while i < hi_v:
            v = load_ptr(tuple_ptr, 24 + i * 8)
            py_tuple_set_item(out, j, v)
            j = j + 1
            i = i + step_v
    else:
        i: int = lo_v
        while i > hi_v:
            if i < 0 or i >= length:
                hi_v = i
            else:
                v = load_ptr(tuple_ptr, 24 + i * 8)
                py_tuple_set_item(out, j, v)
                j = j + 1
                i = i + step_v
    return out


@c_abi_export("py_tuple_concat")
def py_tuple_concat(a, b):
    if a is None:
        return None
    if b is None:
        return None
    la: int = load_i64(a, 16)
    lb: int = load_i64(b, 16)
    out = py_tuple_new(la + lb)
    i: int = 0
    while i < la:
        v = load_ptr(a, 24 + i * 8)
        py_tuple_set_item(out, i, v)
        i = i + 1
    j: int = 0
    while j < lb:
        v = load_ptr(b, 24 + j * 8)
        py_tuple_set_item(out, la + j, v)
        j = j + 1
    return out
