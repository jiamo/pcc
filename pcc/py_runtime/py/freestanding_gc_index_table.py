"""Freestanding pointer indexes shared by all production GC backends.

The tables are open-addressed arrays of 24-byte slots::

    key: ptr, node: ptr, state: i8, padding: 7 bytes

They deliberately depend only on ``pcc.unsafe`` raw memory operations and the
freestanding allocator ABI.  ``src/py_gc_index_table.c`` remains a host-C
differential oracle; the production pcc-Python archive links this module.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_ptr
from pcc.unsafe import (
    calloc,
    define_global_i64,
    define_global_ptr_null,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i64,
    load_i8,
    load_ptr,
    logical_shift_right_i64,
    null,
    wrapping_mul_i64,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
    store_i64,
    store_i8,
    store_ptr,
)

__pcc_freestanding__ = True


define_global_ptr_null("pcc_py_gc_primary_slots")
define_global_i64("pcc_py_gc_primary_cap", 0)
define_global_i64("pcc_py_gc_primary_count", 0)
define_global_i64("pcc_py_gc_primary_used", 0)

define_global_ptr_null("pcc_py_gc_object_slots")
define_global_i64("pcc_py_gc_object_cap", 0)
define_global_i64("pcc_py_gc_object_count", 0)
define_global_i64("pcc_py_gc_object_used", 0)

# Key-only provenance set for managed objects that deliberately have no GC
# object node (backend 0, graph leaves, and explicitly registered runtime
# objects).  A slot is either NULL or one exact managed pointer; callers hold
# the common GC graph lock, so lookup never needs to inspect the candidate's
# header or guess from its virtual address.
define_global_ptr_null("pcc_py_gc_managed_pointer_slots")
define_global_i64("pcc_py_gc_managed_pointer_cap", 0)
define_global_i64("pcc_py_gc_managed_pointer_count", 0)

define_global_ptr_null("pcc_py_gc_forwarding_slots")
define_global_i64("pcc_py_gc_forwarding_cap", 0)
define_global_i64("pcc_py_gc_forwarding_count", 0)
define_global_i64("pcc_py_gc_forwarding_used", 0)

define_global_ptr_null("pcc_py_gc_forwarding_target_slots")
define_global_i64("pcc_py_gc_forwarding_target_cap", 0)
define_global_i64("pcc_py_gc_forwarding_target_count", 0)
define_global_i64("pcc_py_gc_forwarding_target_used", 0)

define_global_ptr_null("pcc_py_gc_identity_slots")
define_global_i64("pcc_py_gc_identity_cap", 0)
define_global_i64("pcc_py_gc_identity_count", 0)
define_global_i64("pcc_py_gc_identity_used", 0)

define_global_ptr_null("pcc_py_gc_frame_slots")
define_global_i64("pcc_py_gc_frame_cap", 0)
define_global_i64("pcc_py_gc_frame_count", 0)
define_global_i64("pcc_py_gc_frame_used", 0)

define_global_ptr_null("pcc_py_gc_zpage_owner_slots")
define_global_i64("pcc_py_gc_zpage_owner_cap", 0)
define_global_i64("pcc_py_gc_zpage_owner_count", 0)
define_global_i64("pcc_py_gc_zpage_owner_used", 0)

define_global_ptr_null("pcc_py_gc_zpage_page_slots")
define_global_i64("pcc_py_gc_zpage_page_cap", 0)
define_global_i64("pcc_py_gc_zpage_page_count", 0)
define_global_i64("pcc_py_gc_zpage_page_used", 0)


@c_abi_export("pcc_gc_index_py_hash_ptr")
def pcc_gc_index_py_hash_ptr(key: c_ptr) -> i64:
    # Fibonacci multiply: consecutive (bump-allocated) addresses previously
    # landed in consecutive slots and linear probing degenerated into giant
    # primary clusters (find_slot dominated GC3/GC4 profiles). Keep bit-exact
    # with py_gc_index_hash_ptr in src/py_gc_index_table.c.
    # -7046029254386353131 == 0x9E3779B97F4A7C15 as a two's-complement i64.
    value: i64 = logical_shift_right_i64(ptr_diff(key, null()), 3)
    value = wrapping_mul_i64(value, -7046029254386353131)
    return value ^ logical_shift_right_i64(value, 32)


@c_abi_export("pcc_gc_index_py_next_pow2")
def pcc_gc_index_py_next_pow2(value: i64) -> i64:
    if value < 8:
        return 8
    power: i64 = 1
    while power < value:
        power = power * 2
    return power


@c_abi_export("pcc_gc_index_py_rehash_capacity")
def pcc_gc_index_py_rehash_capacity(
    cap: i64, count: i64, minimum: i64
) -> i64:
    desired: i64 = (count + 1) * 4
    if desired < minimum:
        desired = minimum
    compact: i64 = pcc_gc_index_py_next_pow2(desired)
    if count + 1 > logical_shift_right_i64(cap, 1):
        grown: i64 = cap * 2
        if grown > compact:
            return grown
        return compact
    if compact < cap:
        return compact
    return cap


@c_abi_export("pcc_gc_index_py_find_slot")
def pcc_gc_index_py_find_slot(slots: c_ptr, cap: i64, key: c_ptr) -> i64:
    # Non-negative means occupied; negative encodes ``-insertion_index - 1``.
    # Backward-shift deletion keeps probe chains gap-free, so slot state is
    # only 0 (empty) or 1 (occupied): churn workloads no longer accumulate
    # tombstones that force same-capacity rehashes (calloc+memset dominated
    # the GC4 longrun profile) or lengthen probe runs.
    mask: i64 = cap - 1
    index: i64 = pcc_gc_index_py_hash_ptr(key) & mask
    while True:
        offset: i64 = index * 24
        state: i64 = load_i8(slots, offset + 16)
        if state == 0:
            return -index - 1
        if ptr_eq(load_ptr(slots, offset), key):
            return index
        index = (index + 1) & mask


@c_abi_export("pcc_gc_index_py_rehash_slots")
def pcc_gc_index_py_rehash_slots(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    used_cell: c_ptr,
    requested_cap: i64,
) -> i64:
    new_cap: i64 = pcc_gc_index_py_next_pow2(requested_cap)
    new_slots = calloc(new_cap, 24)
    if ptr_is_null(new_slots):
        return -1

    old_slots = load_ptr(slots_cell, 0)
    old_cap: i64 = load_i64(cap_cell, 0)
    new_used: i64 = 0
    if ptr_is_null(old_slots) == 0:
        index: i64 = 0
        while index < old_cap:
            old_offset: i64 = index * 24
            if (
                load_i8(old_slots, old_offset + 16)
                == 1
            ):
                key = load_ptr(old_slots, old_offset)
                result: i64 = pcc_gc_index_py_find_slot(new_slots, new_cap, key)
                new_index: i64 = -result - 1
                new_offset: i64 = new_index * 24
                store_ptr(new_slots, new_offset, key)
                store_ptr(
                    new_slots,
                    new_offset + 8,
                    load_ptr(old_slots, old_offset + 8),
                )
                store_i8(
                    new_slots,
                    new_offset + 16,
                    1,
                )
                new_used = new_used + 1
            index = index + 1
        free(old_slots)

    store_ptr(slots_cell, 0, new_slots)
    store_i64(cap_cell, 0, new_cap)
    store_i64(used_cell, 0, new_used)
    return 0


@c_abi_export("pcc_gc_index_py_find")
def pcc_gc_index_py_find(
    slots_cell: c_ptr, cap_cell: c_ptr, key: c_ptr, reject_tagged: i64
) -> c_ptr:
    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots) or ptr_is_null(key):
        return null()
    if reject_tagged != 0 and is_tagged_int(key):
        return null()
    result: i64 = pcc_gc_index_py_find_slot(slots, load_i64(cap_cell, 0), key)
    if result < 0:
        return null()
    return load_ptr(
        slots,
        result * 24 + 8,
    )


@c_abi_export("pcc_gc_index_py_insert")
def pcc_gc_index_py_insert(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
    key: c_ptr,
    node: c_ptr,
    reject_tagged: i64,
    require_node: i64,
    initial_cap: i64,
) -> i64:
    if ptr_is_null(key):
        return -1
    if reject_tagged != 0 and is_tagged_int(key):
        return -1
    if require_node != 0 and ptr_is_null(node):
        return -1

    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots):
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, initial_cap
            )
            != 0
        ):
            return -1
        slots = load_ptr(slots_cell, 0)

    cap: i64 = load_i64(cap_cell, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, key)
    if result >= 0:
        return 0

    used: i64 = load_i64(used_cell, 0)
    count: i64 = load_i64(count_cell, 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        new_cap: i64 = pcc_gc_index_py_rehash_capacity(cap, count, initial_cap)
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, new_cap
            )
            != 0
        ):
            return -1
        slots = load_ptr(slots_cell, 0)
        cap = load_i64(cap_cell, 0)
        used = load_i64(used_cell, 0)
        result = pcc_gc_index_py_find_slot(slots, cap, key)

    index: i64 = -result - 1
    offset: i64 = index * 24
    if load_i8(slots, offset + 16) == 0:
        store_i64(used_cell, 0, used + 1)
    store_ptr(slots, offset, key)
    store_ptr(slots, offset + 8, node)
    store_i8(slots, offset + 16, 1)
    store_i64(count_cell, 0, count + 1)
    return 1


@c_abi_export("pcc_gc_index_py_upsert")
def pcc_gc_index_py_upsert(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
    key: c_ptr,
    node: c_ptr,
    reject_tagged: i64,
    initial_cap: i64,
) -> i64:
    if ptr_is_null(key) or ptr_is_null(node):
        return -1
    if reject_tagged != 0 and is_tagged_int(key):
        return -1

    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots):
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, initial_cap
            )
            != 0
        ):
            return -1
        slots = load_ptr(slots_cell, 0)

    cap: i64 = load_i64(cap_cell, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, key)
    if result >= 0:
        store_ptr(
            slots,
            result * 24 + 8,
            node,
        )
        return 0

    used: i64 = load_i64(used_cell, 0)
    count: i64 = load_i64(count_cell, 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        new_cap: i64 = pcc_gc_index_py_rehash_capacity(cap, count, initial_cap)
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, new_cap
            )
            != 0
        ):
            return -1
        slots = load_ptr(slots_cell, 0)
        cap = load_i64(cap_cell, 0)
        used = load_i64(used_cell, 0)
        result = pcc_gc_index_py_find_slot(slots, cap, key)

    index: i64 = -result - 1
    offset: i64 = index * 24
    if load_i8(slots, offset + 16) == 0:
        store_i64(used_cell, 0, used + 1)
    store_ptr(slots, offset, key)
    store_ptr(slots, offset + 8, node)
    store_i8(slots, offset + 16, 1)
    store_i64(count_cell, 0, count + 1)
    return 1


@c_abi_export("pcc_gc_index_py_replace_raw")
def pcc_gc_index_py_replace_raw(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
    key: c_ptr,
    node: c_ptr,
    initial_cap: i64,
) -> c_ptr:
    if ptr_is_null(key) or ptr_is_null(node):
        return node

    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots):
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, initial_cap
            )
            != 0
        ):
            return node
        slots = load_ptr(slots_cell, 0)

    cap: i64 = load_i64(cap_cell, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, key)
    if result >= 0:
        offset: i64 = result * 24
        old = load_ptr(slots, offset + 8)
        store_ptr(slots, offset + 8, node)
        return old

    used: i64 = load_i64(used_cell, 0)
    count: i64 = load_i64(count_cell, 0)
    if used + 1 > logical_shift_right_i64(cap, 1):
        new_cap: i64 = pcc_gc_index_py_rehash_capacity(cap, count, initial_cap)
        if (
            pcc_gc_index_py_rehash_slots(
                slots_cell, cap_cell, used_cell, new_cap
            )
            != 0
        ):
            return node
        slots = load_ptr(slots_cell, 0)
        cap = load_i64(cap_cell, 0)
        used = load_i64(used_cell, 0)
        result = pcc_gc_index_py_find_slot(slots, cap, key)

    index: i64 = -result - 1
    offset = index * 24
    if load_i8(slots, offset + 16) == 0:
        store_i64(used_cell, 0, used + 1)
    store_ptr(slots, offset, key)
    store_ptr(slots, offset + 8, node)
    store_i8(slots, offset + 16, 1)
    store_i64(count_cell, 0, count + 1)
    return null()


@c_abi_export("pcc_gc_index_py_remove")
def pcc_gc_index_py_remove(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
    key: c_ptr,
    reject_tagged: i64,
) -> c_ptr:
    slots = load_ptr(slots_cell, 0)
    if ptr_is_null(slots) or ptr_is_null(key):
        return null()
    if reject_tagged != 0 and is_tagged_int(key):
        return null()
    cap: i64 = load_i64(cap_cell, 0)
    result: i64 = pcc_gc_index_py_find_slot(slots, cap, key)
    if result < 0:
        return null()
    node = load_ptr(slots, result * 24 + 8)
    # Backward-shift deletion: close the probe-chain gap instead of writing
    # a tombstone. An entry at ``probe`` (home slot ``home``) may fill the
    # hole iff its home lies cyclically outside (hole, probe], i.e.
    # (probe - home) mod cap >= (probe - hole) mod cap. Loop cost is the
    # probe-run length past the hole (short at <=50% load with the
    # Fibonacci hash). No engine caller iterates slots across removes, so
    # moving entries here is safe for every index instance.
    mask: i64 = cap - 1
    hole: i64 = result
    probe: i64 = result
    done: i64 = 0
    while done == 0:
        probe = (probe + 1) & mask
        probe_off: i64 = probe * 24
        if load_i8(slots, probe_off + 16) != 1:
            done: i64 = 1
        else:
            probe_key = load_ptr(slots, probe_off)
            home: i64 = pcc_gc_index_py_hash_ptr(probe_key) & mask
            if ((probe - home) & mask) >= ((probe - hole) & mask):
                hole_off: i64 = hole * 24
                store_ptr(slots, hole_off, probe_key)
                store_ptr(
                    slots,
                    hole_off + 8,
                    load_ptr(slots, probe_off + 8),
                )
                store_i8(slots, hole_off + 16, 1)
                hole = probe
    clear_off: i64 = hole * 24
    store_ptr(slots, clear_off, null())
    store_ptr(slots, clear_off + 8, null())
    store_i8(slots, clear_off + 16, 0)
    store_i64(count_cell, 0, load_i64(count_cell, 0) - 1)
    store_i64(used_cell, 0, load_i64(used_cell, 0) - 1)
    return node


@c_abi_export("pcc_gc_index_py_clear")
def pcc_gc_index_py_clear(
    slots_cell: c_ptr,
    cap_cell: c_ptr,
    count_cell: c_ptr,
    used_cell: c_ptr,
) -> None:
    free(load_ptr(slots_cell, 0))
    store_ptr(slots_cell, 0, null())
    store_i64(cap_cell, 0, 0)
    store_i64(count_cell, 0, 0)
    store_i64(used_cell, 0, 0)


@c_abi_export("pcc_gc_managed_pointer_find_slot")
def _managed_pointer_find_slot(slots, cap: i64, key) -> i64:
    # Non-negative means occupied; negative encodes ``-insertion_index - 1``.
    mask: i64 = cap - 1
    index: i64 = pcc_gc_index_py_hash_ptr(key) & mask
    while True:
        candidate = load_ptr(slots, index * 8)
        if ptr_is_null(candidate) != 0:
            return -index - 1
        if ptr_eq(candidate, key) != 0:
            return index
        index = (index + 1) & mask


@c_abi_export("pcc_gc_managed_pointer_rehash")
def _managed_pointer_rehash(requested_cap: i64) -> i64:
    new_cap: i64 = pcc_gc_index_py_next_pow2(requested_cap)
    new_slots = calloc(new_cap, 8)
    if ptr_is_null(new_slots) != 0:
        return -1
    old_slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    old_cap: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0)
    index: i64 = 0
    while index < old_cap:
        key = load_ptr(old_slots, index * 8)
        if ptr_is_null(key) == 0:
            result: i64 = _managed_pointer_find_slot(new_slots, new_cap, key)
            store_ptr(new_slots, (-result - 1) * 8, key)
        index = index + 1
    free(old_slots)
    global_store_ptr("pcc_py_gc_managed_pointer_slots", new_slots)
    store_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0, new_cap)
    return 0


@c_abi_export("pcc_gc_managed_pointer_index_contains")
def pcc_gc_managed_pointer_index_contains(obj) -> i64:
    slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    if ptr_is_null(slots) != 0 or ptr_is_null(obj) != 0:
        return 0
    if is_tagged_int(obj) != 0:
        return 0
    result: i64 = _managed_pointer_find_slot(
        slots,
        load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0),
        obj,
    )
    if result >= 0:
        return 1
    return 0


@c_abi_export("pcc_gc_managed_pointer_index_insert")
def pcc_gc_managed_pointer_index_insert(obj) -> i64:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return -1
    slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    if ptr_is_null(slots) != 0:
        if _managed_pointer_rehash(256) != 0:
            return -1
        slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    cap: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0)
    result: i64 = _managed_pointer_find_slot(slots, cap, obj)
    if result >= 0:
        return 0
    count: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_count"), 0)
    if count + 1 > logical_shift_right_i64(cap, 1):
        if _managed_pointer_rehash(cap * 2) != 0:
            return -1
        slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
        cap = load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0)
        result = _managed_pointer_find_slot(slots, cap, obj)
    store_ptr(slots, (-result - 1) * 8, obj)
    store_i64(global_addr("pcc_py_gc_managed_pointer_count"), 0, count + 1)
    return 1


@c_abi_export("pcc_gc_managed_pointer_index_remove")
def pcc_gc_managed_pointer_index_remove(obj) -> i64:
    slots = global_load_ptr("pcc_py_gc_managed_pointer_slots")
    if ptr_is_null(slots) != 0 or ptr_is_null(obj) != 0:
        return 0
    if is_tagged_int(obj) != 0:
        return 0
    cap: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_cap"), 0)
    hole: i64 = _managed_pointer_find_slot(slots, cap, obj)
    if hole < 0:
        return 0
    mask: i64 = cap - 1
    probe: i64 = hole
    done: i64 = 0
    while done == 0:
        probe = (probe + 1) & mask
        key = load_ptr(slots, probe * 8)
        if ptr_is_null(key) != 0:
            done: i64 = 1
        else:
            home: i64 = pcc_gc_index_py_hash_ptr(key) & mask
            if ((probe - home) & mask) >= ((probe - hole) & mask):
                store_ptr(slots, hole * 8, key)
                hole = probe
    store_ptr(slots, hole * 8, null())
    count: i64 = load_i64(global_addr("pcc_py_gc_managed_pointer_count"), 0)
    store_i64(global_addr("pcc_py_gc_managed_pointer_count"), 0, count - 1)
    return 1


@c_abi_export("py_gc_index_find")
def py_gc_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_primary_slots"),
        global_addr("pcc_py_gc_primary_cap"),
        obj,
        1,
    )


@c_abi_export("py_gc_index_insert")
def py_gc_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_primary_slots"),
        global_addr("pcc_py_gc_primary_cap"),
        global_addr("pcc_py_gc_primary_count"),
        global_addr("pcc_py_gc_primary_used"),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("py_gc_index_remove")
def py_gc_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_primary_slots"),
        global_addr("pcc_py_gc_primary_cap"),
        global_addr("pcc_py_gc_primary_count"),
        global_addr("pcc_py_gc_primary_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_object_index_find")
def pcc_gc_object_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_object_slots"),
        global_addr("pcc_py_gc_object_cap"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_object_index_insert")
def pcc_gc_object_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_object_slots"),
        global_addr("pcc_py_gc_object_cap"),
        global_addr("pcc_py_gc_object_count"),
        global_addr("pcc_py_gc_object_used"),
        obj,
        node,
        1,
        1,
        16384,
    )


@c_abi_export("pcc_gc_object_index_remove")
def pcc_gc_object_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_object_slots"),
        global_addr("pcc_py_gc_object_cap"),
        global_addr("pcc_py_gc_object_count"),
        global_addr("pcc_py_gc_object_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_object_index_clear")
def pcc_gc_object_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_object_slots"),
        global_addr("pcc_py_gc_object_cap"),
        global_addr("pcc_py_gc_object_count"),
        global_addr("pcc_py_gc_object_used"),
    )


@c_abi_export("pcc_gc_forwarding_index_find")
def pcc_gc_forwarding_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_forwarding_slots"),
        global_addr("pcc_py_gc_forwarding_cap"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_forwarding_index_insert")
def pcc_gc_forwarding_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_forwarding_slots"),
        global_addr("pcc_py_gc_forwarding_cap"),
        global_addr("pcc_py_gc_forwarding_count"),
        global_addr("pcc_py_gc_forwarding_used"),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("pcc_gc_forwarding_index_remove")
def pcc_gc_forwarding_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_forwarding_slots"),
        global_addr("pcc_py_gc_forwarding_cap"),
        global_addr("pcc_py_gc_forwarding_count"),
        global_addr("pcc_py_gc_forwarding_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_forwarding_index_clear")
def pcc_gc_forwarding_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_forwarding_slots"),
        global_addr("pcc_py_gc_forwarding_cap"),
        global_addr("pcc_py_gc_forwarding_count"),
        global_addr("pcc_py_gc_forwarding_used"),
    )


@c_abi_export("pcc_gc_forwarding_target_index_find")
def pcc_gc_forwarding_target_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_forwarding_target_slots"),
        global_addr("pcc_py_gc_forwarding_target_cap"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_forwarding_target_index_insert")
def pcc_gc_forwarding_target_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_forwarding_target_slots"),
        global_addr("pcc_py_gc_forwarding_target_cap"),
        global_addr("pcc_py_gc_forwarding_target_count"),
        global_addr("pcc_py_gc_forwarding_target_used"),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("pcc_gc_forwarding_target_index_upsert")
def pcc_gc_forwarding_target_index_upsert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_upsert(
        global_addr("pcc_py_gc_forwarding_target_slots"),
        global_addr("pcc_py_gc_forwarding_target_cap"),
        global_addr("pcc_py_gc_forwarding_target_count"),
        global_addr("pcc_py_gc_forwarding_target_used"),
        obj,
        node,
        1,
        256,
    )


@c_abi_export("pcc_gc_forwarding_target_index_remove")
def pcc_gc_forwarding_target_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_forwarding_target_slots"),
        global_addr("pcc_py_gc_forwarding_target_cap"),
        global_addr("pcc_py_gc_forwarding_target_count"),
        global_addr("pcc_py_gc_forwarding_target_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_forwarding_target_index_clear")
def pcc_gc_forwarding_target_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_forwarding_target_slots"),
        global_addr("pcc_py_gc_forwarding_target_cap"),
        global_addr("pcc_py_gc_forwarding_target_count"),
        global_addr("pcc_py_gc_forwarding_target_used"),
    )


@c_abi_export("pcc_gc_identity_index_find")
def pcc_gc_identity_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_identity_slots"),
        global_addr("pcc_py_gc_identity_cap"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_identity_index_insert")
def pcc_gc_identity_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_identity_slots"),
        global_addr("pcc_py_gc_identity_cap"),
        global_addr("pcc_py_gc_identity_count"),
        global_addr("pcc_py_gc_identity_used"),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("pcc_gc_identity_index_remove")
def pcc_gc_identity_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_identity_slots"),
        global_addr("pcc_py_gc_identity_cap"),
        global_addr("pcc_py_gc_identity_count"),
        global_addr("pcc_py_gc_identity_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_identity_index_clear")
def pcc_gc_identity_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_identity_slots"),
        global_addr("pcc_py_gc_identity_cap"),
        global_addr("pcc_py_gc_identity_count"),
        global_addr("pcc_py_gc_identity_used"),
    )


@c_abi_export("pcc_gc_frame_index_find")
def pcc_gc_frame_index_find(slots: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        slots,
        0,
    )


@c_abi_export("pcc_gc_frame_index_insert")
def pcc_gc_frame_index_insert(slots: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        global_addr("pcc_py_gc_frame_count"),
        global_addr("pcc_py_gc_frame_used"),
        slots,
        node,
        0,
        0,
        256,
    )


@c_abi_export("pcc_gc_frame_index_replace")
def pcc_gc_frame_index_replace(slots: c_ptr, node: c_ptr) -> c_ptr:
    return pcc_gc_index_py_replace_raw(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        global_addr("pcc_py_gc_frame_count"),
        global_addr("pcc_py_gc_frame_used"),
        slots,
        node,
        256,
    )


@c_abi_export("pcc_gc_frame_index_remove")
def pcc_gc_frame_index_remove(slots: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        global_addr("pcc_py_gc_frame_count"),
        global_addr("pcc_py_gc_frame_used"),
        slots,
        0,
    )


@c_abi_export("pcc_gc_frame_index_clear")
def pcc_gc_frame_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_frame_slots"),
        global_addr("pcc_py_gc_frame_cap"),
        global_addr("pcc_py_gc_frame_count"),
        global_addr("pcc_py_gc_frame_used"),
    )


@c_abi_export("pcc_gc_zpage_owner_index_find")
def pcc_gc_zpage_owner_index_find(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_zpage_owner_index_insert")
def pcc_gc_zpage_owner_index_insert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        global_addr("pcc_py_gc_zpage_owner_count"),
        global_addr("pcc_py_gc_zpage_owner_used"),
        obj,
        node,
        1,
        0,
        256,
    )


@c_abi_export("pcc_gc_zpage_owner_index_upsert")
def pcc_gc_zpage_owner_index_upsert(obj: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_upsert(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        global_addr("pcc_py_gc_zpage_owner_count"),
        global_addr("pcc_py_gc_zpage_owner_used"),
        obj,
        node,
        1,
        256,
    )


@c_abi_export("pcc_gc_zpage_owner_index_remove")
def pcc_gc_zpage_owner_index_remove(obj: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        global_addr("pcc_py_gc_zpage_owner_count"),
        global_addr("pcc_py_gc_zpage_owner_used"),
        obj,
        1,
    )


@c_abi_export("pcc_gc_zpage_owner_index_clear")
def pcc_gc_zpage_owner_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_zpage_owner_slots"),
        global_addr("pcc_py_gc_zpage_owner_cap"),
        global_addr("pcc_py_gc_zpage_owner_count"),
        global_addr("pcc_py_gc_zpage_owner_used"),
    )


@c_abi_export("pcc_gc_zpage_page_index_find")
def pcc_gc_zpage_page_index_find(page: c_ptr) -> c_ptr:
    return pcc_gc_index_py_find(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        page,
        0,
    )


@c_abi_export("pcc_gc_zpage_page_index_insert")
def pcc_gc_zpage_page_index_insert(page: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_insert(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        global_addr("pcc_py_gc_zpage_page_count"),
        global_addr("pcc_py_gc_zpage_page_used"),
        page,
        node,
        0,
        0,
        256,
    )


@c_abi_export("pcc_gc_zpage_page_index_upsert")
def pcc_gc_zpage_page_index_upsert(page: c_ptr, node: c_ptr) -> i64:
    return pcc_gc_index_py_upsert(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        global_addr("pcc_py_gc_zpage_page_count"),
        global_addr("pcc_py_gc_zpage_page_used"),
        page,
        node,
        0,
        256,
    )


@c_abi_export("pcc_gc_zpage_page_index_remove")
def pcc_gc_zpage_page_index_remove(page: c_ptr) -> c_ptr:
    return pcc_gc_index_py_remove(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        global_addr("pcc_py_gc_zpage_page_count"),
        global_addr("pcc_py_gc_zpage_page_used"),
        page,
        0,
    )


@c_abi_export("pcc_gc_zpage_page_index_clear")
def pcc_gc_zpage_page_index_clear() -> None:
    pcc_gc_index_py_clear(
        global_addr("pcc_py_gc_zpage_page_slots"),
        global_addr("pcc_py_gc_zpage_page_cap"),
        global_addr("pcc_py_gc_zpage_page_count"),
        global_addr("pcc_py_gc_zpage_page_used"),
    )


@c_abi_export("pcc_gc_ptr_index_tls_pool_drain")
def pcc_gc_ptr_index_tls_pool_drain() -> None:
    # Open addressing owns no thread-local chained-entry pool.
    return
