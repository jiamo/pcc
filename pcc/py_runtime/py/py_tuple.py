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
    global_addr,
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
py_decref         = extern("py_decref",        (c_ptr,),                             c_void)
py_list_len       = extern("py_list_len",      (c_ptr,),                             c_int64)
py_list_get       = extern("py_list_get",      (c_ptr, c_int64),                     c_ptr)
py_obj_len        = extern("py_obj_len",       (c_ptr,),                             c_int64)
py_obj_getitem_i64 = extern("py_obj_getitem_i64", (c_ptr, c_int64),                  c_ptr)
py_int_value_i64  = extern("py_int_value_i64", (c_ptr,),                             c_int64)
py_err_occurred   = extern("py_err_occurred",  (),                                   c_int64)
py_gc_track       = extern("py_gc_track",       (c_ptr,),                             c_void)
py_exc_new        = extern("py_exc_new",        (c_int64, c_ptr),                     c_ptr)
py_raise          = extern("py_raise",          (c_ptr,),                             c_void)
pcc_gc_store_ptr  = extern("pcc_gc_store_ptr",  (c_ptr, c_ptr, c_ptr),                c_void)
pcc_gc_load_ptr   = extern("pcc_gc_load_ptr",   (c_ptr, c_ptr),                       c_ptr)
pcc_gc_alloc      = extern("pcc_gc_alloc",      (c_int64, c_int32, c_int32),          c_ptr)
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


@c_abi_export("py_tuple_from_list")
def py_tuple_from_list(lst):
    # New tuple from a pcc list's elements. Mirrors py_tuple_from_list in
    # py_tuple.c; used by the dynamic-call lowering to normalize mixed
    # ``f(a, *rest)`` argument lists to the tuple the callable ABI requires.
    if ptr_is_null(lst):
        return null()
    n: int = py_list_len(lst)
    out = py_tuple_new(n)
    if ptr_is_null(out):
        return null()
    i: int = 0
    while i < n:
        v = py_list_get(lst, i)        # new ref
        py_tuple_set_item(out, i, v)   # increfs
        py_decref(v)
        i = i + 1
    return out


@c_abi_export("py_tuple_from_splat")
def py_tuple_from_splat(seq):
    if ptr_is_null(seq):
        return null()
    if not _ptr_can_have_header(seq):
        return null()

    tag: int = load_i32(seq, 8)
    n: int = -1
    if tag == 7:
        n = load_i64(seq, 16)
    elif tag == 5:
        n = py_list_len(seq)
    else:
        n = py_obj_len(seq)
        if py_err_occurred() != 0:
            return null()
    if n < 0:
        py_raise(py_exc_new(6, cstr("tuple() argument is not iterable")))  # PY_EXC_TYPEERROR
        return null()

    out = py_tuple_new(n)
    if ptr_is_null(out):
        return null()

    i: int = 0
    if tag == 7:
        while i < n:
            v = pcc_gc_load_ptr(seq, ptr_add(seq, 24 + i * 8))
            py_tuple_set_item(out, i, v)
            i = i + 1
        return out
    if tag == 5:
        items = load_ptr(seq, 32)
        while i < n:
            v = pcc_gc_load_ptr(seq, ptr_add(items, i * 8))
            py_tuple_set_item(out, i, v)
            i = i + 1
        return out

    while i < n:
        v = py_obj_getitem_i64(seq, i)
        if ptr_is_null(v) and py_err_occurred() != 0:
            py_decref(out)
            return null()
        py_tuple_set_item(out, i, v)
        if ptr_is_null(v) == 0:
            py_decref(v)
        i = i + 1
    return out


@c_abi_export("py_tuple_set_item")
def py_tuple_set_item(tuple_ptr, i: int, item) -> None:
    if ptr_is_null(tuple_ptr) != 0:
        return
    tuple_len: int = load_i64(tuple_ptr, 16)
    if i < 0 or i >= tuple_len:
        return
    slot_offset: int = 24 + i * 8
    slot = ptr_add(tuple_ptr, slot_offset)
    if (
        load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
        and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
    ):
        py_incref(item)
        store_ptr(slot, 0, item)
    else:
        pcc_gc_store_ptr(tuple_ptr, slot, item)
    if _tuple_item_can_participate_in_cycle(item) != 0:
        flags = load_i32(tuple_ptr, 12)
        if (flags & 2) == 0:
            py_gc_track(tuple_ptr)


@c_abi_export("py_tuple_get")
def py_tuple_get(tuple_ptr, i: int):
    if ptr_is_null(tuple_ptr) != 0:
        return null()
    tuple_len: int = load_i64(tuple_ptr, 16)
    if i < 0:
        i = i + tuple_len
    if i < 0 or i >= tuple_len:
        # Non-raising: internal callers rely on the silent-NULL contract.
        # User-level t[i] subscripts route to py_tuple_getitem, which raises.
        return null()
    slot_offset: int = 24 + i * 8
    v = pcc_gc_load_ptr(tuple_ptr, ptr_add(tuple_ptr, slot_offset))
    py_incref(v)
    return v


@c_abi_export("py_tuple_get_known")
def py_tuple_get_known(tuple_ptr, i: int):
    # Adapter-only helper: generated function-call ABI args are known tuples.
    # Keep py_tuple_get's owned-ref result but skip its defensive shape checks.
    if ptr_is_null(tuple_ptr) != 0:
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


@c_abi_export("py_tuple_getitem")
def py_tuple_getitem(tuple_ptr, i: int):
    # t[i] subscript: like py_tuple_get but raises IndexError on out-of-range so
    # try/except can catch it. Mirrors py_tuple_getitem in py_tuple.c; py_tuple_get
    # stays non-raising for other callers. Negative indices normalize.
    if ptr_is_null(tuple_ptr) != 0:
        return null()
    tuple_len: int = load_i64(tuple_ptr, 16)
    if i < 0:
        i = i + tuple_len
    if i < 0 or i >= tuple_len:
        py_raise(py_exc_new(5, cstr("tuple index out of range")))  # PY_EXC_INDEXERROR
        return null()
    slot_offset: int = 24 + i * 8
    v = pcc_gc_load_ptr(tuple_ptr, ptr_add(tuple_ptr, slot_offset))
    py_incref(v)
    return v


@c_abi_export("py_tuple_len")
def py_tuple_len(tuple_ptr) -> int:
    if ptr_is_null(tuple_ptr) != 0:
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
