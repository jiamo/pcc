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
    global_addr,
    is_tagged_int,
    load_i32,
    ptr_add,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
    untag_int,
)

py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
pcc_gc_backend4_zpage_register_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_register_owner_payload_span",
    (c_ptr, c_ptr, c_int64),
    c_int64,
)
py_obj_hash = extern("py_obj_hash", (c_ptr,), c_int64)
py_obj_eq = extern("py_obj_eq", (c_ptr, c_ptr), c_int32)
py_str_eq = extern("py_str_eq", (c_ptr, c_ptr), c_int32)
py_gc_track = extern("py_gc_track", (c_ptr,), c_void)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_note_slot_write_barrier = extern(
    "pcc_gc_note_slot_write_barrier",
    (c_ptr, c_ptr, c_ptr),
    c_void,
)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)

py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_exc_new_with_value = extern("py_exc_new_with_value", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_obj_iter = extern("py_obj_iter", (c_ptr,), c_ptr)
py_obj_next = extern("py_obj_next", (c_ptr,), c_ptr)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
py_exc_matches = extern("py_exc_matches", (c_ptr, c_ptr), c_int64)
py_clear_exception = extern("py_clear_exception", (), c_void)


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


def _ptr_is_dict(o) -> bool:
    if not _ptr_can_have_header(o):
        return False
    return load_i32(o, 8) == 6


def _keys_equal(entry_key, key) -> int:
    if ptr_eq(entry_key, key) != 0:
        return 1
    if is_tagged_int(entry_key) != 0 and is_tagged_int(key) != 0:
        return 0
    if is_tagged_int(entry_key) == 0 and is_tagged_int(key) == 0:
        if load_i32(entry_key, 8) == 4 and load_i32(key, 8) == 4:
            if py_str_eq(entry_key, key) != 0:
                return 1
            return 0
    if py_obj_eq(entry_key, key) != 0:
        return 1
    return 0


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
    store_i64(d, 16, 0)  # size
    store_i64(d, 48, 0)  # entries_used
    pcc_gc_backend4_zpage_register_owner_payload_span(d, entries, capacity * 24)
    return 0


def _perturb_shift5(perturb: int) -> int:
    # Mirror ``(uint64_t)perturb >> 5`` while the pcc-Python runtime exposes
    # only signed i64 arithmetic.  Arithmetic shift differs from logical
    # shift by exactly 2**59 when the input's high bit is set.
    shifted: int = perturb >> 5
    if perturb < 0:
        shifted = shifted + 576460752303423488
    return shifted


def _lookup(d, hash_val: int, key) -> int:
    # Pack two int64 results into i64 return: high 32 = slot, low 32 =
    # entry_idx. Both fit because capacity is bounded. We return i64 here
    # as (slot << 32) | (entry_idx_or_minus1 & 0xFFFFFFFF).
    capacity: int = load_i64(d, 24)
    indices = load_ptr(d, 32)
    entries = load_ptr(d, 40)
    if capacity <= 0:
        return 0xFFFFFFFF
    if ptr_is_null(indices) != 0:
        return 0xFFFFFFFF
    if ptr_is_null(entries) != 0:
        return 0xFFFFFFFF
    mask: int = capacity - 1
    read_barrier_enabled: int = load_i32(global_addr("pcc_gc_read_barrier_enabled"), 0)
    perturb: int = hash_val
    j: int = hash_val & mask
    first_tombstone: int = -1
    probes: int = 0
    limit: int = capacity * 2

    while probes < limit:
        ix: int = load_i64(indices, j * 8)
        if ix == -1:  # PY_DICT_EMPTY
            slot: int = j
            if first_tombstone >= 0:
                slot = first_tombstone
            return (slot << 32) | (0xFFFFFFFF)
        if ix == -2:  # PY_DICT_TOMBSTONE
            if first_tombstone < 0:
                first_tombstone = j
        else:
            entry_off: int = ix * 24
            key_slot = ptr_add(entries, entry_off + 8)
            ek = load_ptr(key_slot, 0)
            if read_barrier_enabled != 0:
                ek = pcc_gc_load_ptr(d, key_slot)
            if ptr_is_null(ek) == 0:
                eh: int = load_i64(entries, entry_off)
                if eh == hash_val:
                    if _keys_equal(ek, key) != 0:
                        return (j << 32) | (ix & 0xFFFFFFFF)
        perturb = _perturb_shift5(perturb)
        j = (j * 5 + perturb + 1) & mask
        probes = probes + 1

    fallback_slot: int = 0
    if first_tombstone >= 0:
        fallback_slot = first_tombstone
    return (fallback_slot << 32) | (0xFFFFFFFF)


def _slot_of(packed: int) -> int:
    return packed >> 32


def _entry_idx_of(packed: int) -> int:
    # Sign-extend low 32 bits: 0xFFFFFFFF unsigned == -1 signed.
    low: int = packed & 0xFFFFFFFF
    if low == 0xFFFFFFFF:
        return -1
    return low


def _entry_key(d, entries, entry_off: int):
    slot = ptr_add(entries, entry_off + 8)
    k = load_ptr(slot, 0)
    if ptr_is_null(k) != 0:
        return k
    if load_i32(global_addr("pcc_gc_read_barrier_enabled"), 0) == 0:
        return k
    return pcc_gc_load_ptr(d, slot)


def _entry_value(d, entries, entry_off: int):
    slot = ptr_add(entries, entry_off + 16)
    v = load_ptr(slot, 0)
    if ptr_is_null(v) != 0:
        return v
    if load_i32(global_addr("pcc_gc_read_barrier_enabled"), 0) == 0:
        return v
    return pcc_gc_load_ptr(d, slot)


def _insert_fresh(d, hash_val: int, key, value) -> None:
    # Inlined lookup + entry move. INCREFs key and value.
    packed: int = _lookup(d, hash_val, key)
    slot: int = _slot_of(packed)
    indices = load_ptr(d, 32)
    entries = load_ptr(d, 40)
    ei: int = load_i64(d, 48)  # entries_used
    store_i64(d, 48, ei + 1)
    entry_off: int = ei * 24
    store_i64(entries, entry_off, hash_val)
    store_ptr(entries, entry_off + 8, null())
    store_ptr(entries, entry_off + 16, null())
    pcc_gc_store_ptr(d, ptr_add(entries, entry_off + 8), key)
    pcc_gc_store_ptr(d, ptr_add(entries, entry_off + 16), value)
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
    pcc_gc_backend4_zpage_register_owner_payload_span(d, new_entries, new_capacity * 24)
    free(old_indices)

    # Walk old entries in insertion order, copying live ones over.
    j: int = 0
    while j < old_entries_used:
        old_off: int = j * 24
        k = _entry_key(d, old_entries, old_off)
        if ptr_is_null(k) == 0:
            h: int = load_i64(old_entries, old_off)
            v = _entry_value(d, old_entries, old_off)
            packed: int = _lookup(d, h, k)
            slot: int = _slot_of(packed)
            ei: int = load_i64(d, 48)
            store_i64(d, 48, ei + 1)
            new_off: int = ei * 24
            store_i64(new_entries, new_off, h)
            store_ptr(new_entries, new_off + 8, k)
            store_ptr(new_entries, new_off + 16, v)
            pcc_gc_note_slot_write_barrier(d, ptr_add(new_entries, new_off + 8), k)
            pcc_gc_note_slot_write_barrier(d, ptr_add(new_entries, new_off + 16), v)
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
    d = pcc_gc_alloc(56, 6, 0)
    if ptr_is_null(d) != 0:
        return null()
    store_i64(d, 16, 0)  # size
    store_i64(d, 24, 0)  # capacity
    store_ptr(d, 32, null())  # indices
    store_ptr(d, 40, null())  # entries
    store_i64(d, 48, 0)  # entries_used
    if _alloc_tables(d, 8) != 0:
        py_decref(d)
        return null()
    py_gc_track(d)
    return d


@c_abi_export("py_dict_set")
def py_dict_set(d, key, value) -> None:
    if not _ptr_is_dict(d):
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
        pcc_gc_store_ptr(d, ptr_add(entries, entry_off + 16), value)
        return
    _insert_fresh(d, h, key, value)
    _maybe_grow(d)


@c_abi_export("py_dict_get")
def py_dict_get(d, key):
    if not _ptr_is_dict(d):
        return null()
    if ptr_is_null(key) != 0:
        return null()
    h: int = py_obj_hash(key)
    packed: int = _lookup(d, h, key)
    ix: int = _entry_idx_of(packed)
    if ix < 0:
        return null()
    entries = load_ptr(d, 40)
    v = pcc_gc_load_ptr(d, ptr_add(entries, ix * 24 + 16))
    if ptr_is_null(v) != 0:
        return null()
    py_incref(v)
    return v


@c_abi_export("py_dict_get_default")
def py_dict_get_default(d, key, default_value):
    v = py_dict_get(d, key)
    if ptr_is_null(v) == 0:
        return v
    py_incref(default_value)
    return default_value


@c_abi_export("py_dict_getitem")
def py_dict_getitem(d, key):
    # d[key] subscript: like py_dict_get but raises KeyError (carrying the key)
    # when absent, so try/except can catch it. Mirrors py_dict_getitem in
    # py_dict.c; py_dict_get stays non-raising for dict.get()/setdefault().
    v = py_dict_get(d, key)
    if ptr_is_null(v) == 0:
        return v
    exc = py_exc_new_with_value(4, key)  # PY_EXC_KEYERROR
    py_raise(exc)
    return null()


@c_abi_export("py_dict_fromkeys")
def py_dict_fromkeys(iterable, value):
    # dict.fromkeys(iterable, value): new dict, each element -> value (caller
    # passes None when omitted). Iterator protocol; clears a terminal
    # StopIteration. Mirrors py_dict_fromkeys in py_dict.c. No break -> use a
    # done flag.
    d = py_dict_new()
    if ptr_is_null(d) != 0:
        return null()
    it = py_obj_iter(iterable)
    if ptr_is_null(it) == 0:
        done: int = 0
        while done == 0:
            k = py_obj_next(it)
            if ptr_is_null(k) != 0:
                if py_err_occurred() != 0:
                    cur = py_current_exception()
                    stop = py_exc_builtin_class(8)  # PY_EXC_STOPITERATION
                    if py_exc_matches(cur, stop) != 0:
                        py_clear_exception()
                done = 1
            else:
                py_dict_set(d, k, value)
                py_decref(k)
        py_decref(it)
    return d


@c_abi_export("py_dict_pop")
def py_dict_pop(d, key):
    v = py_dict_get(d, key)
    if ptr_is_null(v) == 0:
        py_dict_del(d, key)
        return v
    exc = py_exc_new_with_value(4, key)  # PY_EXC_KEYERROR
    py_raise(exc)
    return null()


@c_abi_export("py_dict_popitem")
def py_dict_popitem(d):
    # Remove+return the LAST-inserted (key,value) as a 2-tuple (dicts are
    # insertion-ordered); KeyError if empty. py_tuple_set_item increfs, so
    # py_dict_del's decref leaves key/value owned by the tuple. No `break`
    # (pcc-Python lacks it): a `found` flag in the loop condition exits.
    entries = load_ptr(d, 40)
    ei: int = load_i64(d, 48)  # entries_used
    i: int = ei - 1
    found: int = 0
    result = null()
    while i >= 0 and found == 0:
        entry_off: int = i * 24
        key = _entry_key(d, entries, entry_off)
        if ptr_is_null(key) == 0:
            val = _entry_value(d, entries, entry_off)
            tup = py_tuple_new(2)
            py_tuple_set_item(tup, 0, key)
            py_tuple_set_item(tup, 1, val)
            py_dict_del(d, key)
            result = tup
            found = 1
        i = i - 1
    if found == 0:
        exc = py_exc_new_with_value(4, null())  # bare KeyError
        py_raise(exc)
        return null()
    return result


@c_abi_export("py_dict_contains")
def py_dict_contains(d, key) -> int:
    if not _ptr_is_dict(d):
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
    if not _ptr_is_dict(d):
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
    ek = _entry_key(d, entries, entry_off)
    ev = _entry_value(d, entries, entry_off)
    py_decref(ek)
    py_decref(ev)
    store_ptr(entries, entry_off + 8, null())
    store_ptr(entries, entry_off + 16, null())
    store_i64(indices, slot * 8, -2)  # PY_DICT_TOMBSTONE
    sz: int = load_i64(d, 16)
    store_i64(d, 16, sz - 1)
    return 0


@c_abi_export("py_dict_clear")
def py_dict_clear(d) -> None:
    if not _ptr_is_dict(d):
        return
    entries = load_ptr(d, 40)
    entries_used: int = load_i64(d, 48)
    i: int = 0
    while i < entries_used:
        off: int = i * 24
        k = _entry_key(d, entries, off)
        if ptr_is_null(k) == 0:
            v = _entry_value(d, entries, off)
            py_decref(k)
            py_decref(v)
            store_i64(entries, off, 0)
            store_ptr(entries, off + 8, null())
            store_ptr(entries, off + 16, null())
        i = i + 1
    indices = load_ptr(d, 32)
    capacity: int = load_i64(d, 24)
    j: int = 0
    while j < capacity:
        store_i64(indices, j * 8, -1)
        j = j + 1
    store_i64(d, 16, 0)
    store_i64(d, 48, 0)


@c_abi_export("py_dict_len")
def py_dict_len(d) -> int:
    if not _ptr_is_dict(d):
        return 0
    return load_i64(d, 16)


@c_abi_export("py_dict_entries_used")
def py_dict_entries_used(d) -> int:
    if not _ptr_is_dict(d):
        return 0
    return load_i64(d, 48)


@c_abi_export("py_dict_entry_key_at")
def py_dict_entry_key_at(d, i: int):
    if not _ptr_is_dict(d):
        return null()
    if i < 0:
        return null()
    entries_used: int = load_i64(d, 48)
    if i >= entries_used:
        return null()
    entries = load_ptr(d, 40)
    k = _entry_key(d, entries, i * 24)
    if ptr_is_null(k) == 0:
        py_incref(k)
    return k


@c_abi_export("py_dict_entry_value_at")
def py_dict_entry_value_at(d, i: int):
    if not _ptr_is_dict(d):
        return null()
    if i < 0:
        return null()
    entries_used: int = load_i64(d, 48)
    if i >= entries_used:
        return null()
    entries = load_ptr(d, 40)
    off: int = i * 24
    k = _entry_key(d, entries, off)
    if ptr_is_null(k) != 0:
        return null()
    v = _entry_value(d, entries, off)
    if ptr_is_null(v) == 0:
        py_incref(v)
    return v


@c_abi_export("py_dict_keys")
def py_dict_keys(d):
    if not _ptr_is_dict(d):
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
        k = _entry_key(d, entries, off)
        if ptr_is_null(k) == 0:
            py_list_append(out, k)
        i = i + 1
    return out


@c_abi_export("py_dict_values")
def py_dict_values(d):
    if not _ptr_is_dict(d):
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
        k = _entry_key(d, entries, off)
        if ptr_is_null(k) == 0:
            v = _entry_value(d, entries, off)
            py_list_append(out, v)
        i = i + 1
    return out


@c_abi_export("py_dict_items")
def py_dict_items(d):
    if not _ptr_is_dict(d):
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
        k = _entry_key(d, entries, off)
        if ptr_is_null(k) == 0:
            v = _entry_value(d, entries, off)
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


@c_abi_export("py_dict_update")
def py_dict_update(dst, src) -> None:
    if not _ptr_is_dict(dst):
        return
    if not _ptr_is_dict(src):
        return
    entries = load_ptr(src, 40)
    entries_used: int = load_i64(src, 48)
    i: int = 0
    while i < entries_used:
        off: int = i * 24
        k = _entry_key(src, entries, off)
        if ptr_is_null(k) == 0:
            v = _entry_value(src, entries, off)
            py_dict_set(dst, k, v)
        i = i + 1
