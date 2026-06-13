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
    cstr,
    free,
    global_load_ptr,
    ptr_add,
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
pcc_gc_backend4_zpage_register_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_register_owner_payload_span",
    (c_ptr, c_ptr, c_int64),
    c_int64,
)
py_obj_hash          = extern("py_obj_hash",          (c_ptr,),                     c_int64)
py_obj_eq            = extern("py_obj_eq",            (c_ptr, c_ptr),               c_int32)
py_exc_new           = extern("py_exc_new",           (c_int64, c_ptr),             c_ptr)
py_raise             = extern("py_raise",             (c_ptr,),                     c_void)
py_gc_track          = extern("py_gc_track",          (c_ptr,),                     c_void)
pcc_gc_store_ptr     = extern("pcc_gc_store_ptr",     (c_ptr, c_ptr, c_ptr),        c_void)
pcc_gc_load_ptr      = extern("pcc_gc_load_ptr",      (c_ptr, c_ptr),               c_ptr)
pcc_gc_note_slot_write_barrier = extern(
    "pcc_gc_note_slot_write_barrier", (c_ptr, c_ptr, c_ptr), c_void,
)
pcc_gc_alloc         = extern("pcc_gc_alloc",         (c_int64, c_int32, c_int32),  c_ptr)
py_list_new          = extern("py_list_new",          (c_int64,),                   c_ptr)
py_list_append       = extern("py_list_append",       (c_ptr, c_ptr),               c_void)


# INITIAL_CAPACITY is intentionally NOT a module-level constant —
# pcc-Python initializes module-level integers in the auto-generated
# main(), which the Makefile strips for library .o builds. Inline 8
# at the call site instead.


def _ptr_is_set(o) -> bool:
    if ptr_is_null(o) != 0:
        return False
    if is_tagged_int(o) != 0:
        return False
    return load_i32(o, 8) == 8


def _alloc_entries(capacity: int):
    # SetEntry is 16 bytes: i64 hash + ptr key.
    total = capacity * 16
    entries = malloc(total)
    if ptr_is_null(entries) != 0:
        return entries
    memset(entries, 0, total)
    return entries


def _entry_key(s, entries, slot_off: int):
    k = load_ptr(entries, slot_off + 8)
    if ptr_is_null(k) != 0:
        return k
    if ptr_eq(k, global_load_ptr("py_set_dummy")) != 0:
        return k
    return pcc_gc_load_ptr(s, ptr_add(entries, slot_off + 8))


def _perturb_shift5(perturb: int) -> int:
    # Mirror ``(uint64_t)perturb >> 5`` while the pcc-Python runtime exposes
    # only signed i64 arithmetic.  Arithmetic shift differs from logical
    # shift by exactly 2**59 when the input's high bit is set.
    shifted: int = perturb >> 5
    if perturb < 0:
        shifted = shifted + 576460752303423488
    return shifted


def _lookup_slot(s, entries, capacity: int, hash_val: int, key) -> int:
    # Returns slot index (>=0) if key is found, or -(slot+1) for the
    # insert target if not found (negative encoding).
    mask: int = capacity - 1
    perturb: int = hash_val
    j: int = hash_val & mask
    first_tombstone: int = -1
    dummy = global_load_ptr("py_set_dummy")
    probes: int = 0
    limit: int = capacity * 2

    while probes < limit:
        slot_off: int = j * 16
        k = _entry_key(s, entries, slot_off)
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
        perturb = _perturb_shift5(perturb)
        j = (j * 5 + perturb + 1) & mask
        probes = probes + 1

    fallback_slot: int = 0
    if first_tombstone >= 0:
        fallback_slot = first_tombstone
    return -(fallback_slot + 1)


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
    pcc_gc_backend4_zpage_register_owner_payload_span(
        s, new_entries, new_capacity * 16
    )

    dummy = global_load_ptr("py_set_dummy")
    i: int = 0
    while i < old_capacity:
        slot_off: int = i * 16
        k = _entry_key(s, old_entries, slot_off)
        if ptr_is_null(k) == 0:
            if ptr_eq(k, dummy) == 0:
                h: int = load_i64(old_entries, slot_off)
                dest = _lookup_slot(s, new_entries, new_capacity, h, k)
                if dest < 0:
                    dest = -(dest + 1)
                dest_off: int = dest * 16
                store_i64(new_entries, dest_off, h)
                store_ptr(new_entries, dest_off + 8, k)
                # Move (no incref/decref): route the migrated key through
                # the slot write barrier so backend #3 (generational) and
                # backend #4 (relocating) observe the new slot. Mirrors the
                # C py_set_rehash and py_dict.py _rehash decomposed move.
                pcc_gc_note_slot_write_barrier(s, ptr_add(new_entries, dest_off + 8), k)
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
    s = pcc_gc_alloc(48, 8, 0)  # sizeof(PySetObject), PY_TYPE_SET
    if ptr_is_null(s) != 0:
        return null()
    store_i64(s, 16, 0)    # size
    store_i64(s, 24, 0)    # capacity
    store_i64(s, 32, 0)    # fill
    store_ptr(s, 40, null())    # entries
    # Alloc initial entries table (capacity = 8, must be power of 2).
    entries = _alloc_entries(8)
    if ptr_is_null(entries) != 0:
        py_decref(s)
        return null()
    store_ptr(s, 40, entries)
    store_i64(s, 24, 8)
    pcc_gc_backend4_zpage_register_owner_payload_span(s, entries, 8 * 16)
    py_gc_track(s)
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
    slot: int = _lookup_slot(s, entries, capacity, h, item)
    if slot >= 0:
        return                      # already present
    slot = -(slot + 1)
    slot_off: int = slot * 16
    prev_key = load_ptr(entries, slot_off + 8)
    was_tombstone: int = 0
    if ptr_is_null(prev_key) == 0:
        if ptr_eq(prev_key, global_load_ptr("py_set_dummy")) != 0:
            was_tombstone = 1
    store_i64(entries, slot_off, h)
    store_ptr(entries, slot_off + 8, null())
    pcc_gc_store_ptr(s, ptr_add(entries, slot_off + 8), item)
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
        key = _entry_key(src, entries, i * 16)
        if ptr_is_null(key) == 0:
            if ptr_eq(key, dummy) == 0:
                py_set_add(dst, key)
        i = i + 1


@c_abi_export("py_set_intersection")
def py_set_intersection(a, b):
    out = py_set_new()
    if ptr_is_null(out) != 0:
        return null()
    if not _ptr_is_set(a):
        return out
    if not _ptr_is_set(b):
        return out
    entries = load_ptr(a, 40)
    capacity: int = load_i64(a, 24)
    dummy = global_load_ptr("py_set_dummy")
    i: int = 0
    while i < capacity:
        key = _entry_key(a, entries, i * 16)
        if ptr_is_null(key) == 0:
            if ptr_eq(key, dummy) == 0:
                if py_set_contains(b, key) != 0:
                    py_set_add(out, key)
        i = i + 1
    return out


@c_abi_export("py_set_difference")
def py_set_difference(a, b):
    out = py_set_new()
    if ptr_is_null(out) != 0:
        return null()
    if not _ptr_is_set(a):
        return out
    b_is_set: bool = _ptr_is_set(b)
    entries = load_ptr(a, 40)
    capacity: int = load_i64(a, 24)
    dummy = global_load_ptr("py_set_dummy")
    i: int = 0
    while i < capacity:
        key = _entry_key(a, entries, i * 16)
        if ptr_is_null(key) == 0:
            if ptr_eq(key, dummy) == 0:
                if not b_is_set:
                    py_set_add(out, key)
                elif py_set_contains(b, key) == 0:
                    py_set_add(out, key)
        i = i + 1
    return out


@c_abi_export("py_set_symmetric_difference")
def py_set_symmetric_difference(a, b):
    # a ^ b = (a - b) | (b - a); mirrors py_set.c::py_set_symmetric_difference.
    out = py_set_new()
    if ptr_is_null(out) != 0:
        return null()
    a_is_set: bool = _ptr_is_set(a)
    b_is_set: bool = _ptr_is_set(b)
    dummy = global_load_ptr("py_set_dummy")
    if a_is_set:
        entries_a = load_ptr(a, 40)
        capacity_a: int = load_i64(a, 24)
        i: int = 0
        while i < capacity_a:
            key = _entry_key(a, entries_a, i * 16)
            if ptr_is_null(key) == 0:
                if ptr_eq(key, dummy) == 0:
                    if not b_is_set:
                        py_set_add(out, key)
                    elif py_set_contains(b, key) == 0:
                        py_set_add(out, key)
            i = i + 1
    if b_is_set:
        entries_b = load_ptr(b, 40)
        capacity_b: int = load_i64(b, 24)
        j: int = 0
        while j < capacity_b:
            key2 = _entry_key(b, entries_b, j * 16)
            if ptr_is_null(key2) == 0:
                if ptr_eq(key2, dummy) == 0:
                    if not a_is_set:
                        py_set_add(out, key2)
                    elif py_set_contains(a, key2) == 0:
                        py_set_add(out, key2)
            j = j + 1
    return out


def _replace_contents(dst, result) -> None:
    # Drop every live key in `dst` (decref + tombstone) without freeing the
    # entries array, then re-add every live key of `result`. Preserves the
    # receiver object identity while replacing its contents.
    if not _ptr_is_set(dst):
        return
    entries = load_ptr(dst, 40)
    capacity: int = load_i64(dst, 24)
    dummy = global_load_ptr("py_set_dummy")
    i: int = 0
    while i < capacity:
        slot_off: int = i * 16
        k = _entry_key(dst, entries, slot_off)
        if ptr_is_null(k) == 0:
            if ptr_eq(k, dummy) == 0:
                py_decref(k)
                store_ptr(entries, slot_off + 8, dummy)   # tombstone
                sz: int = load_i64(dst, 16)
                store_i64(dst, 16, sz - 1)
        i = i + 1
    # py_set_update re-adds each live key of `result`.
    py_set_update(dst, result)


@c_abi_export("py_set_intersection_update")
def py_set_intersection_update(dst, other) -> None:
    result = py_set_intersection(dst, other)
    if ptr_is_null(result) != 0:
        return
    _replace_contents(dst, result)
    py_decref(result)


@c_abi_export("py_set_difference_update")
def py_set_difference_update(dst, other) -> None:
    result = py_set_difference(dst, other)
    if ptr_is_null(result) != 0:
        return
    _replace_contents(dst, result)
    py_decref(result)


@c_abi_export("py_set_symmetric_difference_update")
def py_set_symmetric_difference_update(dst, other) -> None:
    result = py_set_symmetric_difference(dst, other)
    if ptr_is_null(result) != 0:
        return
    _replace_contents(dst, result)
    py_decref(result)


@c_abi_export("py_set_issubset")
def py_set_issubset(a, b) -> int:
    if not _ptr_is_set(a):
        return 0
    if not _ptr_is_set(b):
        return 0
    size_a: int = load_i64(a, 16)
    size_b: int = load_i64(b, 16)
    if size_a > size_b:
        return 0
    entries = load_ptr(a, 40)
    capacity: int = load_i64(a, 24)
    dummy = global_load_ptr("py_set_dummy")
    i: int = 0
    while i < capacity:
        key = _entry_key(a, entries, i * 16)
        if ptr_is_null(key) == 0:
            if ptr_eq(key, dummy) == 0:
                if py_set_contains(b, key) == 0:
                    return 0
        i = i + 1
    return 1


@c_abi_export("py_set_issuperset")
def py_set_issuperset(a, b) -> int:
    return py_set_issubset(b, a)


@c_abi_export("py_set_items")
def py_set_items(s):
    if not _ptr_is_set(s):
        return null()
    size: int = load_i64(s, 16)
    cap_hint: int = size
    if cap_hint <= 0:
        cap_hint = 4
    out = py_list_new(cap_hint)
    if ptr_is_null(out) != 0:
        return null()
    entries = load_ptr(s, 40)
    capacity: int = load_i64(s, 24)
    dummy = global_load_ptr("py_set_dummy")
    i: int = 0
    while i < capacity:
        key = _entry_key(s, entries, i * 16)
        if ptr_is_null(key) == 0:
            if ptr_eq(key, dummy) == 0:
                py_list_append(out, key)
        i = i + 1
    return out


@c_abi_export("py_set_pop")
def py_set_pop(s):
    if not _ptr_is_set(s):
        return null()
    size: int = load_i64(s, 16)
    if size <= 0:
        py_raise(py_exc_new(4, cstr("pop from an empty set")))
        return null()
    entries = load_ptr(s, 40)
    capacity: int = load_i64(s, 24)
    dummy = global_load_ptr("py_set_dummy")
    i: int = 0
    while i < capacity:
        slot_off: int = i * 16
        key = _entry_key(s, entries, slot_off)
        if ptr_is_null(key) == 0:
            if ptr_eq(key, dummy) == 0:
                store_ptr(entries, slot_off + 8, dummy)
                store_i64(s, 16, size - 1)
                return key
        i = i + 1
    py_raise(py_exc_new(4, cstr("pop from an empty set")))
    return null()


@c_abi_export("py_set_contains")
def py_set_contains(s, item) -> int:
    if ptr_is_null(s) != 0:
        return 0
    if ptr_is_null(item) != 0:
        return 0
    entries = load_ptr(s, 40)
    capacity: int = load_i64(s, 24)
    h: int = py_obj_hash(item)
    slot: int = _lookup_slot(s, entries, capacity, h, item)
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
    slot: int = _lookup_slot(s, entries, capacity, h, item)
    if slot < 0:
        return -1
    slot_off: int = slot * 16
    k = _entry_key(s, entries, slot_off)
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
