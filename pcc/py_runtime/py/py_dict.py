"""Phase 4c.12: pcc-Python port of py_dict.c.

Compact-dict: indices[] is the open-addressing probe table holding
i64 slots that are either PY_DICT_EMPTY (-1), PY_DICT_TOMBSTONE (-2),
or an index into entries[]. entries[] is insertion-ordered for
deterministic iteration.

PyDictObject layout (from py_internal.h):
    offset  0   PyObjectHeader   (16 bytes)
    offset 16   size             (i64)
    offset 24   capacity         (i64)
    offset 32   indices          (i64* — pointer to indices array)
    offset 40   entries          (DictEntry* — pointer to entries array)
    offset 48   entries_used     (i64)
    total: 56 bytes (PyDictObject is sizeof = 56)

DictEntry layout:
    offset  0   hash             (i64)
    offset  8   key              (PyObject*)
    offset 16   value            (PyObject*)
    total: 24 bytes

PY_TYPE_DICT     = 6
PY_DICT_EMPTY    = -1
PY_DICT_TOMBSTONE= -2
INITIAL_CAPACITY = 8 (must be power of 2; inlined per the
                     module-init gotcha — see feedback memory).
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
    free,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)

py_incref            = extern("py_incref",            (c_ptr,),                    c_void)
py_decref            = extern("py_decref",            (c_ptr,),                    c_void)
py_obj_hash          = extern("py_obj_hash",          (c_ptr,),                    c_int64)
py_obj_eq            = extern("py_obj_eq",            (c_ptr, c_ptr),              c_int32)

py_list_new          = extern("py_list_new",          (c_int64,),                  c_ptr)
py_list_append       = extern("py_list_append",       (c_ptr, c_ptr),              c_void)
py_tuple_new         = extern("py_tuple_new",         (c_int64,),                  c_ptr)
py_tuple_set_item    = extern("py_tuple_set_item",    (c_ptr, c_int64, c_ptr),     c_void)


def _alloc_tables(d, capacity: int) -> int:
    # Returns 0 on success, -1 on alloc failure.
    indices = malloc(capacity * 8)
    if ptr_is_null(indices) != 0:
        return -1
    entries = malloc(capacity * 24)
    if ptr_is_null(entries) != 0:
        free(indices)
        return -1
    # Init indices[] to PY_DICT_EMPTY (-1).
    i: int = 0
    while i < capacity:
        store_i64(indices, i * 8, -1)
        i = i + 1
    # entries[] is only read for 0..entries_used, so no init.
    store_ptr(d, 32, indices)
    store_ptr(d, 40, entries)
    store_i64(d, 24, capacity)
    store_i64(d, 16, 0)            # size
    store_i64(d, 48, 0)            # entries_used
    return 0


def _lookup(d, hash_val: int, key) -> int:
    # Pack two int64 results into i64 return: high 32 = slot, low 32 =
    # entry_idx. Both fit because capacity is bounded. We return i64 here
    # as (slot << 32) | (entry_idx_or_minus1 & 0xFFFFFFFF).
    capacity: int = load_i64(d, 24)
    indices = load_ptr(d, 32)
    entries = load_ptr(d, 40)
    mask: int = capacity - 1
    perturb: int = hash_val
    j: int = hash_val & mask
    first_tombstone: int = -1

    while True:
        ix: int = load_i64(indices, j * 8)
        if ix == -1:                       # PY_DICT_EMPTY
            slot: int = j
            if first_tombstone >= 0:
                slot = first_tombstone
            return (slot << 32) | (0xFFFFFFFF)
        if ix == -2:                       # PY_DICT_TOMBSTONE
            if first_tombstone < 0:
                first_tombstone = j
        else:
            entry_off: int = ix * 24
            ek = load_ptr(entries, entry_off + 8)
            if ptr_is_null(ek) == 0:
                eh: int = load_i64(entries, entry_off)
                if eh == hash_val:
                    if ptr_eq(ek, key) != 0:
                        return (j << 32) | (ix & 0xFFFFFFFF)
                    if py_obj_eq(ek, key) != 0:
                        return (j << 32) | (ix & 0xFFFFFFFF)
        perturb = perturb >> 5
        j = (j * 5 + perturb + 1) & mask


def _slot_of(packed: int) -> int:
    return packed >> 32


def _entry_idx_of(packed: int) -> int:
    # Sign-extend low 32 bits: 0xFFFFFFFF unsigned == -1 signed.
    low: int = packed & 0xFFFFFFFF
    if low == 0xFFFFFFFF:
        return -1
    return low


def _insert_fresh(d, hash_val: int, key, value) -> None:
    # Inlined lookup + entry move. INCREFs key and value.
    packed: int = _lookup(d, hash_val, key)
    slot: int = _slot_of(packed)
    indices = load_ptr(d, 32)
    entries = load_ptr(d, 40)
    ei: int = load_i64(d, 48)            # entries_used
    store_i64(d, 48, ei + 1)
    entry_off: int = ei * 24
    py_incref(key)
    py_incref(value)
    store_i64(entries, entry_off, hash_val)
    store_ptr(entries, entry_off + 8, key)
    store_ptr(entries, entry_off + 16, value)
    store_i64(indices, slot * 8, ei)
    sz: int = load_i64(d, 16)
    store_i64(d, 16, sz + 1)


def _rehash(d, new_capacity: int) -> int:
    old_entries = load_ptr(d, 40)
    old_indices = load_ptr(d, 32)
    old_entries_used: int = load_i64(d, 48)

    new_indices = malloc(new_capacity * 8)
    if ptr_is_null(new_indices) != 0:
        return -1
    new_entries = malloc(new_capacity * 24)
    if ptr_is_null(new_entries) != 0:
        free(new_indices)
        return -1
    i: int = 0
    while i < new_capacity:
        store_i64(new_indices, i * 8, -1)
        i = i + 1

    store_ptr(d, 32, new_indices)
    store_ptr(d, 40, new_entries)
    store_i64(d, 24, new_capacity)
    store_i64(d, 16, 0)
    store_i64(d, 48, 0)
    free(old_indices)

    # Walk old entries in insertion order, copying live ones over.
    j: int = 0
    while j < old_entries_used:
        old_off: int = j * 24
        k = load_ptr(old_entries, old_off + 8)
        if ptr_is_null(k) == 0:
            h: int = load_i64(old_entries, old_off)
            v = load_ptr(old_entries, old_off + 16)
            packed: int = _lookup(d, h, k)
            slot: int = _slot_of(packed)
            ei: int = load_i64(d, 48)
            store_i64(d, 48, ei + 1)
            new_off: int = ei * 24
            store_i64(new_entries, new_off, h)
            store_ptr(new_entries, new_off + 8, k)
            store_ptr(new_entries, new_off + 16, v)
            store_i64(new_indices, slot * 8, ei)
            sz: int = load_i64(d, 16)
            store_i64(d, 16, sz + 1)
        j = j + 1
    free(old_entries)
    return 0


def _maybe_grow(d) -> int:
    capacity: int = load_i64(d, 24)
    entries_used: int = load_i64(d, 48)
    threshold: int = (capacity * 2) // 3
    if entries_used <= threshold:
        return 0
    new_cap: int = capacity
    size: int = load_i64(d, 16)
    if size > threshold // 2:
        new_cap = capacity * 2
    return _rehash(d, new_cap)


@c_abi_export("py_dict_new")
def py_dict_new():
    d = malloc(56)
    if ptr_is_null(d) != 0:
        return null()
    store_i64(d, 0, 1)              # refcount
    store_i32(d, 8, 6)              # PY_TYPE_DICT
    store_i32(d, 12, 0)             # flags
    store_i64(d, 16, 0)             # size
    store_i64(d, 24, 0)             # capacity
    store_ptr(d, 32, null())   # indices
    store_ptr(d, 40, null())   # entries
    store_i64(d, 48, 0)             # entries_used
    if _alloc_tables(d, 8) != 0:
        free(d)
        return null()
    return d


@c_abi_export("py_dict_set")
def py_dict_set(d, key, value) -> None:
    if ptr_is_null(d) != 0:
        return
    if ptr_is_null(key) != 0:
        return
    h: int = py_obj_hash(key)
    packed: int = _lookup(d, h, key)
    ix: int = _entry_idx_of(packed)
    if ix >= 0:
        # Update existing — replace value, keep key.
        entries = load_ptr(d, 40)
        entry_off: int = ix * 24
        old_value = load_ptr(entries, entry_off + 16)
        py_incref(value)
        py_decref(old_value)
        store_ptr(entries, entry_off + 16, value)
        return
    _insert_fresh(d, h, key, value)
    _maybe_grow(d)


@c_abi_export("py_dict_get")
def py_dict_get(d, key):
    if ptr_is_null(d) != 0:
        return null()
    if ptr_is_null(key) != 0:
        return null()
    h: int = py_obj_hash(key)
    packed: int = _lookup(d, h, key)
    ix: int = _entry_idx_of(packed)
    if ix < 0:
        return null()
    entries = load_ptr(d, 40)
    v = load_ptr(entries, ix * 24 + 16)
    py_incref(v)
    return v


@c_abi_export("py_dict_get_default")
def py_dict_get_default(d, key, default_value):
    v = py_dict_get(d, key)
    if ptr_is_null(v) == 0:
        return v
    py_incref(default_value)
    return default_value


@c_abi_export("py_dict_contains")
def py_dict_contains(d, key) -> int:
    if ptr_is_null(d) != 0:
        return 0
    if ptr_is_null(key) != 0:
        return 0
    h: int = py_obj_hash(key)
    packed: int = _lookup(d, h, key)
    ix: int = _entry_idx_of(packed)
    if ix >= 0:
        return 1
    return 0


@c_abi_export("py_dict_del")
def py_dict_del(d, key) -> int:
    if ptr_is_null(d) != 0:
        return -1
    if ptr_is_null(key) != 0:
        return -1
    h: int = py_obj_hash(key)
    packed: int = _lookup(d, h, key)
    ix: int = _entry_idx_of(packed)
    if ix < 0:
        return -1
    slot: int = _slot_of(packed)
    entries = load_ptr(d, 40)
    indices = load_ptr(d, 32)
    entry_off: int = ix * 24
    ek = load_ptr(entries, entry_off + 8)
    ev = load_ptr(entries, entry_off + 16)
    py_decref(ek)
    py_decref(ev)
    store_ptr(entries, entry_off + 8, null())
    store_ptr(entries, entry_off + 16, null())
    store_i64(indices, slot * 8, -2)        # PY_DICT_TOMBSTONE
    sz: int = load_i64(d, 16)
    store_i64(d, 16, sz - 1)
    return 0


@c_abi_export("py_dict_len")
def py_dict_len(d) -> int:
    if ptr_is_null(d) != 0:
        return 0
    return load_i64(d, 16)


@c_abi_export("py_dict_keys")
def py_dict_keys(d):
    if ptr_is_null(d) != 0:
        return null()
    size: int = load_i64(d, 16)
    cap_hint: int = size
    if cap_hint <= 0:
        cap_hint = 4
    out = py_list_new(cap_hint)
    if ptr_is_null(out) != 0:
        return null()
    entries = load_ptr(d, 40)
    entries_used: int = load_i64(d, 48)
    i: int = 0
    while i < entries_used:
        off: int = i * 24
        k = load_ptr(entries, off + 8)
        if ptr_is_null(k) == 0:
            py_list_append(out, k)
        i = i + 1
    return out


@c_abi_export("py_dict_values")
def py_dict_values(d):
    if ptr_is_null(d) != 0:
        return null()
    size: int = load_i64(d, 16)
    cap_hint: int = size
    if cap_hint <= 0:
        cap_hint = 4
    out = py_list_new(cap_hint)
    if ptr_is_null(out) != 0:
        return null()
    entries = load_ptr(d, 40)
    entries_used: int = load_i64(d, 48)
    i: int = 0
    while i < entries_used:
        off: int = i * 24
        k = load_ptr(entries, off + 8)
        if ptr_is_null(k) == 0:
            v = load_ptr(entries, off + 16)
            py_list_append(out, v)
        i = i + 1
    return out


@c_abi_export("py_dict_items")
def py_dict_items(d):
    if ptr_is_null(d) != 0:
        return null()
    size: int = load_i64(d, 16)
    cap_hint: int = size
    if cap_hint <= 0:
        cap_hint = 4
    out = py_list_new(cap_hint)
    if ptr_is_null(out) != 0:
        return null()
    entries = load_ptr(d, 40)
    entries_used: int = load_i64(d, 48)
    i: int = 0
    while i < entries_used:
        off: int = i * 24
        k = load_ptr(entries, off + 8)
        if ptr_is_null(k) == 0:
            v = load_ptr(entries, off + 16)
            pair = py_tuple_new(2)
            if ptr_is_null(pair) != 0:
                py_decref(out)
                return null()
            py_tuple_set_item(pair, 0, k)
            py_tuple_set_item(pair, 1, v)
            py_list_append(out, pair)
            py_decref(pair)
        i = i + 1
    return out
