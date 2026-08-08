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

Header layout and public type tags are imported from the generated
C-header-derived py_abi_constants module.
"""

from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.py_runtime.py.py_abi_constants import (
    PYLISTOBJECT_CAPACITY_OFFSET,
    PYLISTOBJECT_ITEMS_OFFSET,
    PYLISTOBJECT_LENGTH_OFFSET,
    PYLISTOBJECT_SIZE,
    PYOBJECTHEADER_TYPE_TAG_OFFSET,
    PYTUPLEOBJECT_ITEMS_OFFSET,
    PYTUPLEOBJECT_LEN_OFFSET,
    PY_TYPE_INT,
    PY_TYPE_LIST,
    PY_TYPE_TUPLE,
)
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_DICT,
)
from pcc.unsafe import (
    cstr,
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
    ptr_to_int,
    realloc,
    store_i32,
    store_i64,
    store_ptr,
    untag_int,
)

py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_obj_eq = extern("py_obj_eq", (c_ptr, c_ptr), c_int32)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_obj_index_i64 = extern("py_obj_index_i64", (c_ptr,), c_int64)
py_obj_iter = extern("py_obj_iter", (c_ptr,), c_ptr)
py_obj_next = extern("py_obj_next", (c_ptr,), c_ptr)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
py_exc_matches = extern("py_exc_matches", (c_ptr, c_ptr), c_int64)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_gc_track = extern("py_gc_track", (c_ptr,), c_void)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_backend4_zpage_register_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_register_owner_payload_span",
    (c_ptr, c_ptr, c_int64),
    c_int64,
)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
py_dict_clear = extern("py_dict_clear", (c_ptr,), c_void)
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
    return load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_LIST


def _list_is_sane(lst, code: int) -> bool:
    if ptr_is_null(lst) != 0:
        return False
    if not _ptr_is_list(lst):
        _debug_bad_container(lst, code)
        return False
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    capacity: int = load_i64(lst, PYLISTOBJECT_CAPACITY_OFFSET)
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
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
    return load_i32(obj, PYOBJECTHEADER_TYPE_TAG_OFFSET)


def _is_none_or_null(o) -> int:
    if ptr_is_null(o):
        return 1
    if ptr_eq(o, global_load_ptr("py_None")):
        return 1
    return 0


def _int_to_i64_or_zero(o) -> int:
    if ptr_is_null(o) != 0:
        return 0
    if is_tagged_int(o) != 0:
        return untag_int(o)
    if load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_INT:
        return 0
    return py_int_value_i64(o)


def _grow_if_needed(l, want: int) -> int:
    if not _list_is_sane(l, -102):
        return -1
    capacity: int = load_i64(l, PYLISTOBJECT_CAPACITY_OFFSET)
    if capacity >= want:
        return 0
    cap: int = capacity
    if cap <= 0:
        cap = 4
    while cap < want:
        cap = cap * 2
    items = load_ptr(l, PYLISTOBJECT_ITEMS_OFFSET)
    new_items = realloc(items, cap * 8)
    if ptr_is_null(new_items):
        return -1
    store_ptr(l, PYLISTOBJECT_ITEMS_OFFSET, new_items)
    store_i64(l, PYLISTOBJECT_CAPACITY_OFFSET, cap)
    pcc_gc_backend4_zpage_register_owner_payload_span(l, new_items, cap * 8)
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
    if initial_capacity > 134217728:
        _debug_bad_container(null(), -100)
        return null()
    l = pcc_gc_alloc(PYLISTOBJECT_SIZE, PY_TYPE_LIST, 0)
    if ptr_is_null(l):
        return null()
    store_i64(l, PYLISTOBJECT_LENGTH_OFFSET, 0)  # length
    cap: int = initial_capacity
    if cap < 4:
        cap = 4
    store_ptr(l, PYLISTOBJECT_ITEMS_OFFSET, null())  # items
    store_i64(l, PYLISTOBJECT_CAPACITY_OFFSET, cap)
    items = malloc(cap * 8)
    if ptr_is_null(items):
        py_decref(l)
        return null()
    store_ptr(l, PYLISTOBJECT_ITEMS_OFFSET, items)
    pcc_gc_backend4_zpage_register_owner_payload_span(l, items, cap * 8)
    py_gc_track(l)
    return l


@c_abi_export("py_list_append")
def py_list_append(lst, item) -> None:
    if not _list_is_sane(lst, -101):
        return
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    if _grow_if_needed(lst, length + 1) != 0:
        py_raise(py_exc_new(19, cstr("list append: out of memory")))  # PY_EXC_MEMORYERROR
        return
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    store_ptr(items, length * 8, null())
    pcc_gc_store_ptr(lst, ptr_add(items, length * 8), item)
    store_i64(lst, PYLISTOBJECT_LENGTH_OFFSET, length + 1)


@c_abi_export("py_list_get")
def py_list_get(lst, i: int):
    if not _list_is_sane(lst, -103):
        return null()
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    idx: int = _normalize_index(i, length, 0)
    if idx < 0:
        return null()
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    v = pcc_gc_load_ptr(lst, ptr_add(items, idx * 8))
    py_incref(v)
    return v


@c_abi_export("py_list_getitem")
def py_list_getitem(lst, i: int):
    # a[i] subscript: like py_list_get but raises IndexError on out-of-range so
    # try/except can catch it. Mirrors py_list_getitem in py_list.c; py_list_get
    # stays non-raising for other callers. Negative indices normalize.
    if not _list_is_sane(lst, -103):
        return null()
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    idx: int = _normalize_index(i, length, 0)
    if idx < 0:
        py_raise(py_exc_new(5, cstr("list index out of range")))  # PY_EXC_INDEXERROR
        return null()
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    v = pcc_gc_load_ptr(lst, ptr_add(items, idx * 8))
    py_incref(v)
    return v


@c_abi_export("py_list_get_i64")
def py_list_get_i64(lst, i: int) -> int:
    if ptr_is_null(lst) != 0:
        return 0
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    idx: int = _normalize_index(i, length, 0)
    if idx < 0:
        return 0
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    v = pcc_gc_load_ptr(lst, ptr_add(items, idx * 8))
    return _int_to_i64_or_zero(v)


@c_abi_export("py_list_get_i64_nonnegative")
def py_list_get_i64_nonnegative(lst, i: int) -> int:
    if ptr_is_null(lst) != 0:
        return 0
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    if i < 0 or i >= length:
        return 0
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    v = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
    return _int_to_i64_or_zero(v)


@c_abi_export("py_list_set")
def py_list_set(lst, i: int, item) -> None:
    # Internal non-raising setter: callers (sort/insert shifts, generator
    # frames) index within bounds by construction. User-visible subscript
    # stores go through py_list_setitem below.
    if not _list_is_sane(lst, -104):
        return
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    idx: int = _normalize_index(i, length, 0)
    if idx < 0:
        return
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    pcc_gc_store_ptr(lst, ptr_add(items, idx * 8), item)


@c_abi_export("py_list_setitem")
def py_list_setitem(lst, i: int, item) -> int:
    # items[i] = v subscript store: like py_list_set but raises IndexError on
    # out-of-range so try/except can catch it (CPython: "list assignment index
    # out of range"). Mirrors py_list_setitem in py_list.c; py_list_set stays
    # non-raising for internal callers. Negative indices normalize.
    if not _list_is_sane(lst, -121):
        return -1
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    idx: int = _normalize_index(i, length, 0)
    if idx < 0:
        py_raise(py_exc_new(5, cstr("list assignment index out of range")))  # PY_EXC_INDEXERROR
        return -1
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    pcc_gc_store_ptr(lst, ptr_add(items, idx * 8), item)
    return 0


@c_abi_export("py_list_len")
def py_list_len(lst) -> int:
    if ptr_is_null(lst) != 0:
        return 0
    return load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)


@c_abi_export("py_list_concat")
def py_list_concat(a, b):
    if not _list_is_sane(a, -106):
        return null()
    if not _list_is_sane(b, -106):
        return null()
    la: int = load_i64(a, PYLISTOBJECT_LENGTH_OFFSET)
    lb: int = load_i64(b, PYLISTOBJECT_LENGTH_OFFSET)
    n: int = la + lb
    cap_hint: int = n
    if cap_hint <= 0:
        cap_hint = 4
    out = py_list_new(cap_hint)
    if ptr_is_null(out):
        return null()
    out_items = load_ptr(out, PYLISTOBJECT_ITEMS_OFFSET)
    a_items = load_ptr(a, PYLISTOBJECT_ITEMS_OFFSET)
    i: int = 0
    while i < la:
        v = pcc_gc_load_ptr(a, ptr_add(a_items, i * 8))
        py_incref(v)
        store_ptr(out_items, i * 8, v)
        i = i + 1
    b_items = load_ptr(b, PYLISTOBJECT_ITEMS_OFFSET)
    j: int = 0
    while j < lb:
        v = pcc_gc_load_ptr(b, ptr_add(b_items, j * 8))
        py_incref(v)
        store_ptr(out_items, (la + j) * 8, v)
        j = j + 1
    store_i64(out, PYLISTOBJECT_LENGTH_OFFSET, n)
    return out


@c_abi_export("py_list_copy")
def py_list_copy(src):
    # list.copy() — shallow copy: fresh list, same elements (element refs
    # shared with the source, incref'd once each). Mirrors py_list_copy in
    # py_list.c. Empty source -> fresh empty list.
    if not _list_is_sane(src, -106):
        return null()
    n: int = load_i64(src, PYLISTOBJECT_LENGTH_OFFSET)
    cap_hint: int = n
    if cap_hint <= 0:
        cap_hint = 4
    out = py_list_new(cap_hint)
    if ptr_is_null(out):
        return null()
    out_items = load_ptr(out, PYLISTOBJECT_ITEMS_OFFSET)
    src_items = load_ptr(src, PYLISTOBJECT_ITEMS_OFFSET)
    i: int = 0
    while i < n:
        v = pcc_gc_load_ptr(src, ptr_add(src_items, i * 8))
        py_incref(v)
        store_ptr(out_items, i * 8, v)
        i = i + 1
    store_i64(out, PYLISTOBJECT_LENGTH_OFFSET, n)
    return out


@c_abi_export("py_list_repeat")
def py_list_repeat(src, count: int):
    if not _list_is_sane(src, -107):
        return null()
    sl: int = load_i64(src, PYLISTOBJECT_LENGTH_OFFSET)
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
    out_items = load_ptr(out, PYLISTOBJECT_ITEMS_OFFSET)
    src_items = load_ptr(src, PYLISTOBJECT_ITEMS_OFFSET)
    pos: int = 0
    k: int = 0
    while k < real_count:
        i: int = 0
        while i < sl:
            v = pcc_gc_load_ptr(src, ptr_add(src_items, i * 8))
            py_incref(v)
            store_ptr(out_items, pos * 8, v)
            pos = pos + 1
            i = i + 1
        k = k + 1
    store_i64(out, PYLISTOBJECT_LENGTH_OFFSET, out_len)
    return out


@c_abi_export("py_list_contains")
def py_list_contains(lst, item) -> int:
    if not _list_is_sane(lst, -108):
        return 0
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    i: int = 0
    while i < length:
        v = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
        if py_obj_eq(v, item) != 0:
            return 1
        i = i + 1
    return 0


def _push_to_list(out, v) -> int:
    # Helper: append v to out with grow-check; return -1 on alloc fail.
    if not _list_is_sane(out, -109):
        return -1
    out_len: int = load_i64(out, PYLISTOBJECT_LENGTH_OFFSET)
    if _grow_if_needed(out, out_len + 1) != 0:
        return -1
    out_items = load_ptr(out, PYLISTOBJECT_ITEMS_OFFSET)
    py_incref(v)
    store_ptr(out_items, out_len * 8, v)
    store_i64(out, PYLISTOBJECT_LENGTH_OFFSET, out_len + 1)
    return 0


def _seq_len(seq) -> int:
    if ptr_is_null(seq):
        return -1
    tag: int = _type_of(seq)
    if tag == PY_TYPE_LIST:  # PY_TYPE_LIST
        if not _list_is_sane(seq, -114):
            return -1
        return load_i64(seq, PYLISTOBJECT_LENGTH_OFFSET)
    if tag == PY_TYPE_TUPLE:  # PY_TYPE_TUPLE
        n: int = load_i64(seq, PYTUPLEOBJECT_LEN_OFFSET)
        if n < 0 or n > 134217728:
            _debug_bad_container(seq, -115)
            return -1
        return n
    return -1


def _seq_get_borrowed(seq, i: int):
    tag: int = _type_of(seq)
    if tag == PY_TYPE_LIST:  # PY_TYPE_LIST
        if not _list_is_sane(seq, -116):
            return null()
        items = load_ptr(seq, PYLISTOBJECT_ITEMS_OFFSET)
        return pcc_gc_load_ptr(seq, ptr_add(items, i * 8))
    if tag == PY_TYPE_TUPLE:  # PY_TYPE_TUPLE
        items = ptr_add(seq, PYTUPLEOBJECT_ITEMS_OFFSET)
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


def _list_delete_index(lst, idx: int) -> None:
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    slot = ptr_add(items, idx * 8)
    old = pcc_gc_load_ptr(lst, slot)
    store_ptr(items, idx * 8, null())
    if ptr_is_null(old) == 0:
        py_decref(old)
    if idx < length - 1:
        src = ptr_add(items, (idx + 1) * 8)
        dst = ptr_add(items, idx * 8)
        memmove(dst, src, (length - idx - 1) * 8)
    store_i64(lst, PYLISTOBJECT_LENGTH_OFFSET, length - 1)


def _list_delete_range(lst, lo: int, hi: int) -> int:
    if hi <= lo:
        return 0
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    i: int = lo
    while i < hi:
        slot = ptr_add(items, i * 8)
        old = pcc_gc_load_ptr(lst, slot)
        store_ptr(items, i * 8, null())
        if ptr_is_null(old) == 0:
            py_decref(old)
        i = i + 1
    if hi < length:
        src = ptr_add(items, hi * 8)
        dst = ptr_add(items, lo * 8)
        memmove(dst, src, (length - hi) * 8)
    store_i64(lst, PYLISTOBJECT_LENGTH_OFFSET, length - (hi - lo))
    return 0


@c_abi_export("py_list_slice")
def py_list_slice(lst, lo, hi, step):
    if not _list_is_sane(lst, -110):
        return null()
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)

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

    out = py_list_new(4)
    if ptr_is_null(out):
        return null()
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)

    if step_v > 0:
        i: int = lo_v
        while i < hi_v:
            v = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
            if _push_to_list(out, v) != 0:
                py_decref(out)
                return null()
            i = i + step_v
    else:
        i: int = lo_v
        while i > hi_v:
            if i < 0 or i >= length:
                # break loop
                hi_v = i  # force exit
            else:
                v = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
                if _push_to_list(out, v) != 0:
                    py_decref(out)
                    return null()
                i = i + step_v
    return out


@c_abi_export("py_list_del_slice")
def py_list_del_slice(lst, lo, hi, step) -> int:
    if not _list_is_sane(lst, -112):
        return -1
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)

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

    if step_v == 1:
        return _list_delete_range(lst, lo_v, hi_v)

    count: int = _slice_count(lo_v, hi_v, step_v)
    if count <= 0:
        return 0
    if step_v > 0:
        idx: int = lo_v + (count - 1) * step_v
        n: int = 0
        while n < count:
            _list_delete_index(lst, idx)
            idx = idx - step_v
            n = n + 1
    else:
        idx: int = lo_v
        n: int = 0
        while n < count:
            _list_delete_index(lst, idx)
            idx = idx + step_v
            n = n + 1
    return 0


@c_abi_export("py_list_extend")
def py_list_extend(a, b) -> None:
    if not _list_is_sane(a, -113):
        return
    if ptr_is_null(b):
        return
    btag: int = _type_of(b)
    if btag == PY_TYPE_LIST:  # PY_TYPE_LIST
        if not _list_is_sane(b, -117):
            return
        bl: int = load_i64(b, PYLISTOBJECT_LENGTH_OFFSET)
        la: int = load_i64(a, PYLISTOBJECT_LENGTH_OFFSET)
        if _grow_if_needed(a, la + bl) != 0:
            py_raise(py_exc_new(19, cstr("list extend: out of memory")))  # PY_EXC_MEMORYERROR
            return
        a_items = load_ptr(a, PYLISTOBJECT_ITEMS_OFFSET)
        b_items = load_ptr(b, PYLISTOBJECT_ITEMS_OFFSET)
        i: int = 0
        while i < bl:
            v = pcc_gc_load_ptr(b, ptr_add(b_items, i * 8))
            # Match py_list_append's grown-slot store: NULL-init the fresh
            # (unzeroed) capacity slot, then route through the collector
            # barrier. pcc_gc_store_ptr increfs v, so the prior manual incref
            # is dropped to keep the net accounting (+1 owned ref) identical.
            store_ptr(a_items, (la + i) * 8, null())
            pcc_gc_store_ptr(a, ptr_add(a_items, (la + i) * 8), v)
            i = i + 1
        store_i64(a, PYLISTOBJECT_LENGTH_OFFSET, la + bl)
        return
    if btag == PY_TYPE_TUPLE:  # PY_TYPE_TUPLE
        # PyTupleObject layout: header(16) + len(16) + items(24, flex array)
        bl: int = load_i64(b, PYTUPLEOBJECT_LEN_OFFSET)
        if bl < 0 or bl > 134217728:
            _debug_bad_container(b, -118)
            return
        la: int = load_i64(a, PYLISTOBJECT_LENGTH_OFFSET)
        if _grow_if_needed(a, la + bl) != 0:
            py_raise(py_exc_new(19, cstr("list extend: out of memory")))  # PY_EXC_MEMORYERROR
            return
        a_items = load_ptr(a, PYLISTOBJECT_ITEMS_OFFSET)
        b_items_base = ptr_add(b, PYTUPLEOBJECT_ITEMS_OFFSET)  # &items[0]
        i: int = 0
        while i < bl:
            v = pcc_gc_load_ptr(b, ptr_add(b_items_base, i * 8))
            # Same grown-slot store idiom as the list branch / py_list_append:
            # NULL-init the fresh (unzeroed) capacity slot, then barrier-store.
            # pcc_gc_store_ptr increfs v, so the manual incref is dropped (net +1).
            store_ptr(a_items, (la + i) * 8, null())
            pcc_gc_store_ptr(a, ptr_add(a_items, (la + i) * 8), v)
            i = i + 1
        store_i64(a, PYLISTOBJECT_LENGTH_OFFSET, la + bl)
        return
    it = py_obj_iter(b)
    if ptr_is_null(it):
        return
    while True:
        item = py_obj_next(it)
        if ptr_is_null(item):
            if py_err_occurred() != 0:
                cur = py_current_exception()
                stop = py_exc_builtin_class(8)  # StopIteration
                if py_exc_matches(cur, stop) != 0:
                    py_clear_exception()
                    break
            py_decref(it)
            return
        py_list_append(a, item)
        py_decref(item)
    py_decref(it)


@c_abi_export("py_list_insert")
def py_list_insert(lst, i: int, item) -> None:
    if not _list_is_sane(lst, -119):
        return
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    idx: int = _normalize_index(i, length, 1)
    if _grow_if_needed(lst, length + 1) != 0:
        py_raise(py_exc_new(19, cstr("list insert: out of memory")))  # PY_EXC_MEMORYERROR
        return
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    if idx < length:
        # Shift tail [idx, length) right by one.
        src = ptr_add(items, idx * 8)
        dst = ptr_add(items, (idx + 1) * 8)
        memmove(dst, src, (length - idx) * 8)
    store_ptr(items, idx * 8, null())
    pcc_gc_store_ptr(lst, ptr_add(items, idx * 8), item)
    store_i64(lst, PYLISTOBJECT_LENGTH_OFFSET, length + 1)


@c_abi_export("py_list_pop")
def py_list_pop(lst, i: int):
    if not _list_is_sane(lst, -120):
        return null()
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    if length == 0:
        py_raise(py_exc_new(5, cstr("pop from empty list")))  # PY_EXC_INDEXERROR
        return null()
    idx: int = 0
    if i == -1:
        idx = length - 1
    else:
        idx = _normalize_index(i, length, 0)
        if idx < 0:
            py_raise(py_exc_new(5, cstr("pop index out of range")))  # PY_EXC_INDEXERROR
            return null()
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    v = pcc_gc_load_ptr(lst, ptr_add(items, idx * 8))
    if idx < length - 1:
        src = ptr_add(items, (idx + 1) * 8)
        dst = ptr_add(items, idx * 8)
        memmove(dst, src, (length - idx - 1) * 8)
    store_i64(lst, PYLISTOBJECT_LENGTH_OFFSET, length - 1)
    return v


@c_abi_export("py_list_remove")
def py_list_remove(lst, item) -> None:
    if not _list_is_sane(lst, -121):
        return
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    i: int = 0
    while i < length:
        v = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
        if py_obj_eq(v, item) != 0:
            store_ptr(items, i * 8, null())
            py_decref(v)
            if i < length - 1:
                src = ptr_add(items, (i + 1) * 8)
                dst = ptr_add(items, i * 8)
                memmove(dst, src, (length - i - 1) * 8)
            store_i64(lst, PYLISTOBJECT_LENGTH_OFFSET, length - 1)
            return
        i = i + 1
    # Not found: raise ValueError. py_exc_new takes the private exception-table
    # tag, not a public object type tag.
    exc = py_exc_new(12, null())
    py_raise(exc)


@c_abi_export("py_list_clear")
def py_list_clear(lst) -> None:
    if not _list_is_sane(lst, -122):
        return
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    store_i64(lst, PYLISTOBJECT_LENGTH_OFFSET, 0)
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    i: int = 0
    while i < length:
        v = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
        store_ptr(items, i * 8, null())
        if ptr_is_null(v) == 0:
            py_decref(v)
        i = i + 1


@c_abi_export("py_obj_clear")
def py_obj_clear(obj) -> None:
    tag: int = _type_of(obj)
    if tag == PY_TYPE_LIST:  # PY_TYPE_LIST
        py_list_clear(obj)
    elif tag == PY_TYPE_DICT:  # PY_TYPE_DICT
        py_dict_clear(obj)


@c_abi_export("py_list_index")
def py_list_index(lst, item) -> int:
    if not _list_is_sane(lst, -123):
        return -1
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    i: int = 0
    while i < length:
        v = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
        if py_obj_eq(v, item) != 0:
            return i
        i = i + 1
    return -1


@c_abi_export("py_list_index_range")
def py_list_index_range(lst, item, start: int, end: int) -> int:
    # Range-aware list.index(item, start, end). Negative bounds offset by the
    # length once, then both clamp into [0, length]; the half-open window
    # [start, end) is scanned. Raise ValueError (PY_EXC_VALUEERROR == 2) and
    # return -1 when absent, matching CPython list.index. The frontend checks
    # py_err_occurred() after the call.
    length: int = 0
    if _list_is_sane(lst, -126):
        length = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    if start < 0:
        start = start + length
        if start < 0:
            start = 0
    elif start > length:
        start = length
    if end < 0:
        end = end + length
        if end < 0:
            end = 0
    elif end > length:
        end = length
    if length > 0:
        items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
        i: int = start
        while i < end:
            v = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
            if py_obj_eq(v, item) != 0:
                return i
            i = i + 1
    py_raise(py_exc_new(2, cstr("list.index(x): x not in list")))
    return -1


@c_abi_export("py_list_count")
def py_list_count(lst, item) -> int:
    if not _list_is_sane(lst, -124):
        return 0
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    total: int = 0
    i: int = 0
    while i < length:
        v = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
        if py_obj_eq(v, item) != 0:
            total = total + 1
        i = i + 1
    return total


@c_abi_export("py_list_reverse")
def py_list_reverse(lst) -> None:
    if not _list_is_sane(lst, -125):
        return
    length: int = load_i64(lst, PYLISTOBJECT_LENGTH_OFFSET)
    items = load_ptr(lst, PYLISTOBJECT_ITEMS_OFFSET)
    i: int = 0
    j: int = length - 1
    while i < j:
        left = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
        right = pcc_gc_load_ptr(lst, ptr_add(items, j * 8))
        py_incref(left)
        py_incref(right)
        pcc_gc_store_ptr(lst, ptr_add(items, i * 8), right)
        pcc_gc_store_ptr(lst, ptr_add(items, j * 8), left)
        py_decref(left)
        py_decref(right)
        i = i + 1
        j = j - 1
