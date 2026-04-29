"""Phase 4c.8: pcc-Python port of py_set.c.

Open-addressing hash set of PyObject* (unordered, unique).

PySetObject layout (from py_internal.h):
    offset  0   PyObjectHeader  (i64 refcount, i32 tag, i32 flags = 16)
    offset 16   size            (i64)
    offset 24   capacity        (i64)
    offset 32   fill            (i64)    (live + tombstones)
    offset 40   entries         (SetEntry*)
    total: 48 bytes

SetEntry layout:
    offset  0   hash            (i64)
    offset  8   key             (PyObject*)
    total: 16 bytes

Tombstone: a slot with key == py_set_dummy (never NULL, never a real heap object).
Empty slot: key == NULL.

Initial capacity: 8 (must be power of 2). Grow at 2/3 load factor.
Probing: CPython-style perturbation (perturb = hash; j = hash & mask;
next: perturb >>= 5; j = (j*5 + perturb + 1) & mask).
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
    memset,
    null,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)

py_incref            = extern("py_incref",            (c_ptr,),                     c_void)
py_decref            = extern("py_decref",            (c_ptr,),                     c_void)
py_obj_hash          = extern("py_obj_hash",          (c_ptr,),                     c_int64)
py_obj_eq            = extern("py_obj_eq",            (c_ptr, c_ptr),               c_int32)


# INITIAL_CAPACITY is intentionally NOT a module-level constant —
# pcc-Python initializes module-level integers in the auto-generated
# main(), which the Makefile strips for library .o builds. Inline 8
# at the call site instead.


def _alloc_entries(capacity: int):
    # SetEntry is 16 bytes: i64 hash + ptr key.
    total = capacity * 16
    entries = malloc(total)
    if ptr_is_null(entries) != 0:
        return entries
    memset(entries, 0, total)
    return entries


def _lookup_slot(entries, capacity: int, hash_val: int, key) -> int:
    # Returns slot index (>=0) if key is found, or -(slot+1) for the
    # insert target if not found (negative encoding).
    mask: int = capacity - 1
    perturb: int = hash_val
    j: int = hash_val & mask
    first_tombstone: int = -1
    dummy = global_load_ptr("py_set_dummy")

    while True:
        slot_off: int = j * 16
        k = load_ptr(entries, slot_off + 8)
        if ptr_is_null(k) != 0:
            if first_tombstone >= 0:
                return -(first_tombstone + 1)
            return -(j + 1)
        if ptr_eq(k, dummy) != 0:
            if first_tombstone < 0:
                first_tombstone = j
        else:
            slot_hash: int = load_i64(entries, slot_off)
            if slot_hash == hash_val:
                if ptr_eq(k, key) != 0:
                    return j
                if py_obj_eq(k, key) != 0:
                    return j
        perturb = perturb >> 5
        j = (j * 5 + perturb + 1) & mask


def _rehash(s, new_capacity: int) -> int:
    # Returns 0 on success, -1 on alloc failure.
    old_entries = load_ptr(s, 40)
    old_capacity: int = load_i64(s, 24)

    new_entries = _alloc_entries(new_capacity)
    if ptr_is_null(new_entries) != 0:
        return -1
    store_ptr(s, 40, new_entries)
    store_i64(s, 24, new_capacity)
    store_i64(s, 16, 0)   # size
    store_i64(s, 32, 0)   # fill

    dummy = global_load_ptr("py_set_dummy")
    i: int = 0
    while i < old_capacity:
        slot_off: int = i * 16
        k = load_ptr(old_entries, slot_off + 8)
        if ptr_is_null(k) == 0:
            if ptr_eq(k, dummy) == 0:
                h: int = load_i64(old_entries, slot_off)
                dest = _lookup_slot(new_entries, new_capacity, h, k)
                if dest < 0:
                    dest = -(dest + 1)
                dest_off: int = dest * 16
                store_i64(new_entries, dest_off, h)
                store_ptr(new_entries, dest_off + 8, k)
                # size++
                sz: int = load_i64(s, 16)
                store_i64(s, 16, sz + 1)
                fl: int = load_i64(s, 32)
                store_i64(s, 32, fl + 1)
        i = i + 1
    free(old_entries)
    return 0


def _maybe_grow(s) -> int:
    capacity: int = load_i64(s, 24)
    fill: int = load_i64(s, 32)
    threshold: int = (capacity * 2) // 3
    if fill <= threshold:
        return 0
    new_cap: int = capacity
    size: int = load_i64(s, 16)
    if size > threshold // 2:
        new_cap = capacity * 2
    return _rehash(s, new_cap)


@c_abi_export("py_set_new")
def py_set_new():
    s = malloc(48)          # sizeof(PySetObject)
    if ptr_is_null(s) != 0:
        return null()
    store_i64(s, 0, 1)     # refcount
    store_i32(s, 8, 8)     # type_tag = PY_TYPE_SET
    store_i32(s, 12, 0)    # flags
    store_i64(s, 16, 0)    # size
    store_i64(s, 24, 0)    # capacity
    store_i64(s, 32, 0)    # fill
    store_ptr(s, 40, null())    # entries
    # Alloc initial entries table (capacity = 8, must be power of 2).
    entries = _alloc_entries(8)
    if ptr_is_null(entries) != 0:
        free(s)
        return null()
    store_ptr(s, 40, entries)
    store_i64(s, 24, 8)
    return s


@c_abi_export("py_set_add")
def py_set_add(s, item) -> None:
    if ptr_is_null(s) != 0:
        return
    if ptr_is_null(item) != 0:
        return
    entries = load_ptr(s, 40)
    capacity: int = load_i64(s, 24)
    h: int = py_obj_hash(item)
    slot: int = _lookup_slot(entries, capacity, h, item)
    if slot >= 0:
        return                      # already present
    slot = -(slot + 1)
    slot_off: int = slot * 16
    prev_key = load_ptr(entries, slot_off + 8)
    was_tombstone: int = 0
    if ptr_is_null(prev_key) == 0:
        if ptr_eq(prev_key, global_load_ptr("py_set_dummy")) != 0:
            was_tombstone = 1
    py_incref(item)
    store_i64(entries, slot_off, h)
    store_ptr(entries, slot_off + 8, item)
    # size++
    sz: int = load_i64(s, 16)
    store_i64(s, 16, sz + 1)
    if was_tombstone == 0:
        fl: int = load_i64(s, 32)
        store_i64(s, 32, fl + 1)
    _maybe_grow(s)


@c_abi_export("py_set_update")
def py_set_update(dst, src) -> None:
    if ptr_is_null(dst) != 0:
        return
    if ptr_is_null(src) != 0:
        return
    if is_tagged_int(src) != 0:
        return
    if load_i32(src, 8) != 8:          # PY_TYPE_SET
        return
    entries = load_ptr(src, 40)
    capacity: int = load_i64(src, 24)
    dummy = global_load_ptr("py_set_dummy")
    i: int = 0
    while i < capacity:
        key = load_ptr(entries, i * 16 + 8)
        if ptr_is_null(key) == 0:
            if ptr_eq(key, dummy) == 0:
                py_set_add(dst, key)
        i = i + 1


@c_abi_export("py_set_contains")
def py_set_contains(s, item) -> int:
    if ptr_is_null(s) != 0:
        return 0
    if ptr_is_null(item) != 0:
        return 0
    entries = load_ptr(s, 40)
    capacity: int = load_i64(s, 24)
    h: int = py_obj_hash(item)
    slot: int = _lookup_slot(entries, capacity, h, item)
    if slot >= 0:
        return 1
    return 0


@c_abi_export("py_set_remove")
def py_set_remove(s, item) -> int:
    if ptr_is_null(s) != 0:
        return -1
    if ptr_is_null(item) != 0:
        return -1
    entries = load_ptr(s, 40)
    capacity: int = load_i64(s, 24)
    h: int = py_obj_hash(item)
    slot: int = _lookup_slot(entries, capacity, h, item)
    if slot < 0:
        return -1
    slot_off: int = slot * 16
    k = load_ptr(entries, slot_off + 8)
    py_decref(k)
    store_ptr(entries, slot_off + 8, global_load_ptr("py_set_dummy"))
    sz: int = load_i64(s, 16)
    store_i64(s, 16, sz - 1)
    return 0


@c_abi_export("py_set_len")
def py_set_len(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    return load_i64(s, 16)
