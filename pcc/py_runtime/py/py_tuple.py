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
from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
    cstr,
    getenv,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
    untag_int,
)


py_incref         = extern("py_incref",        (c_ptr,),                             c_void)
py_int_value_i64  = extern("py_int_value_i64", (c_ptr,),                             c_int64)
py_gc_track       = extern("py_gc_track",       (c_ptr,),                             c_void)
pcc_gc_store_ptr  = extern("pcc_gc_store_ptr",  (c_ptr, c_ptr, c_ptr),                c_void)
pcc_gc_load_ptr   = extern("pcc_gc_load_ptr",   (c_ptr, c_ptr),                       c_ptr)
pcc_gc_alloc      = extern("pcc_gc_alloc",      (c_int64, c_int32, c_int32),          c_ptr)
_pcc_debug_bad_incref = extern("pcc_debug_bad_incref", (c_ptr, c_int32), c_void)
_pcc_debug_check_tuple_slot = extern(
    "pcc_debug_check_tuple_slot", (c_ptr, c_int64, c_int64, c_ptr), c_int32
)


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


def _tuple_item_can_participate_in_cycle(item, depth: int = 0) -> int:
    if ptr_is_null(item) != 0:
        return 0
    if is_tagged_int(item) != 0:
        return 0
    if not _ptr_can_have_header(item):
        return 0
    if depth < 0:
        depth = 0
    if depth > 64:
        return 0

    tag: int = load_i32(item, 8)
    if tag >= 100:
        return 1
    if tag == 5 or tag == 6 or tag == 8 or tag == 9:
        return 1
    if tag == 10 or tag == 11 or tag == 12 or tag == 14 or tag == 15:
        return 1
    if tag == 20 or tag == 19 or tag == 21:
        return 1
    if tag == 7:
        length: int = load_i64(item, 16)
        i: int = 0
        while i < length:
            child = pcc_gc_load_ptr(item, ptr_add(item, 24 + i * 8))
            if _tuple_item_can_participate_in_cycle(child, depth + 1) != 0:
                return 1
            i = i + 1
        return 0
    return 0


@c_abi_export("py_tuple_new")
def py_tuple_new(n: int):
    if n < 0:
        n = 0
    if n > 134217728:
        _debug_bad_container(null(), -130)
        return null()
    bytes_total: int = 24 + n * 8          # OFFSET_ITEMS + n * SIZEOF_PTR
    t = pcc_gc_alloc(bytes_total, 7, 0)
    if ptr_is_null(t):
        return null()
    store_i64(t, 16, n)             # len = n
    if n > 0:
        items_ptr = ptr_add(t, 24)
        memset(items_ptr, 0, n * 8)
    return t


@c_abi_export("py_tuple_set_item")
def py_tuple_set_item(tuple_ptr, i: int, item) -> None:
    if not _tuple_is_sane(tuple_ptr, -131):
        return
    tuple_len: int = load_i64(tuple_ptr, 16)
    if i < 0 or i >= tuple_len:
        return
    _pcc_debug_check_tuple_slot(tuple_ptr, i, tuple_len, item)
    slot_offset: int = 24 + i * 8
    pcc_gc_store_ptr(tuple_ptr, ptr_add(tuple_ptr, slot_offset), item)
    if _tuple_item_can_participate_in_cycle(item) != 0:
        flags = load_i32(tuple_ptr, 12)
        if (flags & 2) == 0:
            py_gc_track(tuple_ptr)


@c_abi_export("py_tuple_get")
def py_tuple_get(tuple_ptr, i: int):
    if not _tuple_is_sane(tuple_ptr, -132):
        return null()
    tuple_len: int = load_i64(tuple_ptr, 16)
    if i < 0:
        i = i + tuple_len
    if i < 0 or i >= tuple_len:
        return null()
    slot_offset: int = 24 + i * 8
    v = pcc_gc_load_ptr(tuple_ptr, ptr_add(tuple_ptr, slot_offset))
    py_incref(v)
    return v


@c_abi_export("py_tuple_len")
def py_tuple_len(tuple_ptr) -> int:
    if not _tuple_is_sane(tuple_ptr, -133):
        return 0
    return load_i64(tuple_ptr, 16)


@c_abi_export("py_tuple_concat")
def py_tuple_concat(a, b):
    if not _tuple_is_sane(a, -135):
        return null()
    if not _tuple_is_sane(b, -136):
        return null()
    la: int = load_i64(a, 16)
    lb: int = load_i64(b, 16)
    out = py_tuple_new(la + lb)
    i: int = 0
    while i < la:
        v = pcc_gc_load_ptr(a, ptr_add(a, 24 + i * 8))
        py_tuple_set_item(out, i, v)
        i = i + 1
    j: int = 0
    while j < lb:
        v = pcc_gc_load_ptr(b, ptr_add(b, 24 + j * 8))
        py_tuple_set_item(out, la + j, v)
        j = j + 1
    return out


@c_abi_export("py_tuple_repeat")
def py_tuple_repeat(tuple_ptr, count: int):
    if not _tuple_is_sane(tuple_ptr, -137):
        return null()
    length: int = load_i64(tuple_ptr, 16)
    repeats: int = count
    if repeats < 0:
        repeats = 0
    out = py_tuple_new(length * repeats)
    if ptr_is_null(out):
        return null()
    dst: int = 0
    k: int = 0
    while k < repeats:
        i: int = 0
        while i < length:
            v = pcc_gc_load_ptr(tuple_ptr, ptr_add(tuple_ptr, 24 + i * 8))
            py_tuple_set_item(out, dst, v)
            dst = dst + 1
            i = i + 1
        k = k + 1
    return out
