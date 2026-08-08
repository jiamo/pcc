"""Freestanding allocator authored in pcc-Python.

Small allocations use eight 64 KiB slab-backed size classes.  Freed slots are
kept on locked LIFO lists and reused, so steady small-object churn stops issuing
``mmap``/``munmap`` calls.  Large and over-aligned allocations retain a direct
page mapping whose base and extent live in the 48-byte header before the user
pointer.  The only platform boundary is ``pcc.unsafe.page_alloc/page_free``.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_ptr
from pcc.unsafe import (
    atomic_clear,
    atomic_load_i64,
    atomic_rmw_i64,
    atomic_test_and_set,
    define_global_i8,
    define_global_i64,
    define_global_ptr_null,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    load_i8,
    load_i64,
    load_ptr,
    mul_overflow_i64,
    null,
    page_alloc,
    page_free,
    ptr_add,
    ptr_diff,
    ptr_is_null,
    store_i64,
    store_i8,
    store_ptr,
    wrapping_mul_i64,
)

__pcc_freestanding__ = True


define_global_i8("pcc_allocator_lock", 0)
define_global_i64("pcc_allocator_mapped", 0)
define_global_i64("pcc_allocator_live_requested", 0)
define_global_i64("pcc_allocator_live_usable", 0)
define_global_ptr_null("pcc_allocator_free_16")
define_global_ptr_null("pcc_allocator_free_32")
define_global_ptr_null("pcc_allocator_free_64")
define_global_ptr_null("pcc_allocator_free_128")
define_global_ptr_null("pcc_allocator_free_256")
define_global_ptr_null("pcc_allocator_free_512")
define_global_ptr_null("pcc_allocator_free_1024")
define_global_ptr_null("pcc_allocator_free_2048")
# Pooling stopped at 2048, so every larger object took its own
# page-rounded mmap.  `__mmap` was the #2 leaf in a stage2 frontend
# worker (536 of ~10000 samples); these classes keep medium objects on
# the slab path instead.
define_global_ptr_null("pcc_allocator_free_4096")
define_global_ptr_null("pcc_allocator_free_8192")
define_global_ptr_null("pcc_allocator_free_16384")


@c_abi_export("pcc_allocator_lock_acquire")
def pcc_allocator_lock_acquire() -> None:
    spins: i64 = 0
    while atomic_test_and_set(
        global_addr("pcc_allocator_lock"), 0, "acquire"
    ) != 0:
        spins = spins + 1


@c_abi_export("pcc_allocator_lock_release")
def pcc_allocator_lock_release() -> None:
    atomic_clear(global_addr("pcc_allocator_lock"), 0, "release")


@c_abi_export("pcc_allocator_account_allocate")
def pcc_allocator_account_allocate(
    requested: i64,
    usable: i64,
    mapped: i64,
) -> None:
    atomic_rmw_i64(
        "add", global_addr("pcc_allocator_live_requested"), 0, requested, "relaxed"
    )
    atomic_rmw_i64(
        "add", global_addr("pcc_allocator_live_usable"), 0, usable, "relaxed"
    )
    if mapped != 0:
        atomic_rmw_i64(
            "add", global_addr("pcc_allocator_mapped"), 0, mapped, "relaxed"
        )


@c_abi_export("pcc_allocator_account_free")
def pcc_allocator_account_free(requested: i64, usable: i64) -> None:
    atomic_rmw_i64(
        "sub", global_addr("pcc_allocator_live_requested"), 0, requested, "relaxed"
    )
    atomic_rmw_i64(
        "sub", global_addr("pcc_allocator_live_usable"), 0, usable, "relaxed"
    )


@c_abi_export("pcc_allocator_mapped_bytes")
def pcc_allocator_mapped_bytes() -> i64:
    return atomic_load_i64(global_addr("pcc_allocator_mapped"), 0, "relaxed")


@c_abi_export("pcc_allocator_live_requested_bytes")
def pcc_allocator_live_requested_bytes() -> i64:
    return atomic_load_i64(
        global_addr("pcc_allocator_live_requested"), 0, "relaxed"
    )


@c_abi_export("pcc_allocator_live_usable_bytes")
def pcc_allocator_live_usable_bytes() -> i64:
    return atomic_load_i64(global_addr("pcc_allocator_live_usable"), 0, "relaxed")


@c_abi_export("pcc_os_heap_in_use_bytes")
def pcc_os_heap_in_use_bytes() -> i64:
    # Bytes currently requested from the production pcc allocator.
    return pcc_allocator_live_requested_bytes()


@c_abi_export("pcc_os_heap_capacity_bytes")
def pcc_os_heap_capacity_bytes() -> i64:
    # Bytes of mapped capacity retained by the production pcc allocator.
    return pcc_allocator_mapped_bytes()


@c_abi_export("pcc_allocator_size_class")
def pcc_allocator_size_class(size: i64) -> i64:
    if size <= 16:
        return 16
    if size <= 32:
        return 32
    if size <= 64:
        return 64
    if size <= 128:
        return 128
    if size <= 256:
        return 256
    if size <= 512:
        return 512
    if size <= 1024:
        return 1024
    if size <= 2048:
        return 2048
    if size <= 4096:
        return 4096
    if size <= 8192:
        return 8192
    if size <= 16384:
        return 16384
    return 0


@c_abi_export("pcc_allocator_take_small")
def pcc_allocator_take_small(usable: i64) -> c_ptr:
    if usable == 16:
        head = global_load_ptr("pcc_allocator_free_16")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_16", load_ptr(head, 0))
        return head
    if usable == 32:
        head = global_load_ptr("pcc_allocator_free_32")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_32", load_ptr(head, 0))
        return head
    if usable == 64:
        head = global_load_ptr("pcc_allocator_free_64")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_64", load_ptr(head, 0))
        return head
    if usable == 128:
        head = global_load_ptr("pcc_allocator_free_128")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_128", load_ptr(head, 0))
        return head
    if usable == 256:
        head = global_load_ptr("pcc_allocator_free_256")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_256", load_ptr(head, 0))
        return head
    if usable == 512:
        head = global_load_ptr("pcc_allocator_free_512")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_512", load_ptr(head, 0))
        return head
    if usable == 1024:
        head = global_load_ptr("pcc_allocator_free_1024")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_1024", load_ptr(head, 0))
        return head
    if usable == 2048:
        head = global_load_ptr("pcc_allocator_free_2048")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_2048", load_ptr(head, 0))
        return head
    if usable == 4096:
        head = global_load_ptr("pcc_allocator_free_4096")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_4096", load_ptr(head, 0))
        return head
    if usable == 8192:
        head = global_load_ptr("pcc_allocator_free_8192")
        if ptr_is_null(head) == 0:
            global_store_ptr("pcc_allocator_free_8192", load_ptr(head, 0))
        return head
    head = global_load_ptr("pcc_allocator_free_16384")
    if ptr_is_null(head) == 0:
        global_store_ptr("pcc_allocator_free_16384", load_ptr(head, 0))
    return head


@c_abi_export("pcc_allocator_put_small")
def pcc_allocator_put_small(ptr, usable: i64) -> None:
    if usable == 16:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_16"))
        global_store_ptr("pcc_allocator_free_16", ptr)
        return
    if usable == 32:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_32"))
        global_store_ptr("pcc_allocator_free_32", ptr)
        return
    if usable == 64:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_64"))
        global_store_ptr("pcc_allocator_free_64", ptr)
        return
    if usable == 128:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_128"))
        global_store_ptr("pcc_allocator_free_128", ptr)
        return
    if usable == 256:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_256"))
        global_store_ptr("pcc_allocator_free_256", ptr)
        return
    if usable == 512:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_512"))
        global_store_ptr("pcc_allocator_free_512", ptr)
        return
    if usable == 1024:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_1024"))
        global_store_ptr("pcc_allocator_free_1024", ptr)
        return
    if usable == 2048:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_2048"))
        global_store_ptr("pcc_allocator_free_2048", ptr)
        return
    if usable == 4096:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_4096"))
        global_store_ptr("pcc_allocator_free_4096", ptr)
        return
    if usable == 8192:
        store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_8192"))
        global_store_ptr("pcc_allocator_free_8192", ptr)
        return
    store_ptr(ptr, 0, global_load_ptr("pcc_allocator_free_16384"))
    global_store_ptr("pcc_allocator_free_16384", ptr)


@c_abi_export("pcc_allocator_initialize_small")
def pcc_allocator_initialize_small(user, slab, usable: i64) -> None:
    store_i64(user, -48, 5783538902897647427)
    store_ptr(user, -40, slab)
    store_i64(user, -32, 0)
    store_i64(user, -24, 0)
    store_i64(user, -16, usable)
    store_i64(user, -8, 16)


@c_abi_export("pcc_allocator_refill_small")
def pcc_allocator_refill_small(usable: i64) -> c_ptr:
    stride: i64 = 64
    count: i64 = 1024
    if usable == 32:
        stride: i64 = 80
        count: i64 = 819
    elif usable == 64:
        stride: i64 = 112
        count: i64 = 585
    elif usable == 128:
        stride: i64 = 176
        count: i64 = 372
    elif usable == 256:
        stride: i64 = 304
        count: i64 = 215
    elif usable == 512:
        stride: i64 = 560
        count: i64 = 117
    elif usable == 1024:
        stride: i64 = 1072
        count: i64 = 61
    elif usable == 2048:
        stride: i64 = 2096
        count: i64 = 31
    elif usable == 4096:
        stride: i64 = 4144
        count: i64 = 15
    elif usable == 8192:
        stride: i64 = 8240
        count: i64 = 7
    elif usable == 16384:
        stride: i64 = 16432
        count: i64 = 3

    slab = page_alloc(65536)
    if ptr_is_null(slab):
        return null()
    atomic_rmw_i64(
        "add", global_addr("pcc_allocator_mapped"), 0, 65536, "relaxed"
    )
    first = ptr_add(slab, 48)
    pcc_allocator_initialize_small(first, slab, usable)
    cursor = first
    i: i64 = 1
    while i < count:
        cursor = ptr_add(cursor, stride)
        pcc_allocator_initialize_small(cursor, slab, usable)
        pcc_allocator_put_small(cursor, usable)
        i = i + 1
    return first


@c_abi_export("pcc_allocator_allocate_raw")
def pcc_allocator_allocate_raw(size: i64, alignment: i64) -> c_ptr:
    if size < 0 or alignment < 8:
        return null()
    if alignment > 1073741824 or (alignment & (alignment - 1)) != 0:
        return null()
    requested: i64 = size
    payload: i64 = size
    if payload == 0:
        payload: i64 = 1
    if payload > 9223372036854771664 - alignment:
        return null()
    total: i64 = payload + 48 + alignment - 1
    mapping_size: i64 = (total + 4095) & -4096
    base = page_alloc(mapping_size)
    if ptr_is_null(base):
        return null()

    candidate = ptr_add(base, 48)
    candidate_address: i64 = ptr_diff(candidate, null())
    aligned_address: i64 = (candidate_address + alignment - 1) & -alignment
    user = ptr_add(base, aligned_address - ptr_diff(base, null()))
    usable: i64 = mapping_size - ptr_diff(user, base)

    store_i64(user, -48, 5783538902897647427)
    store_ptr(user, -40, base)
    store_i64(user, -32, mapping_size)
    store_i64(user, -24, requested)
    store_i64(user, -16, usable)
    store_i64(user, -8, alignment)
    pcc_allocator_account_allocate(requested, usable, mapping_size)
    return user


@c_abi_export("malloc")
def pcc_malloc(size: i64) -> c_ptr:
    if size < 0:
        return null()
    usable: i64 = pcc_allocator_size_class(size)
    if usable != 0:
        pcc_allocator_lock_acquire()
        user = pcc_allocator_take_small(usable)
        if ptr_is_null(user):
            user = pcc_allocator_refill_small(usable)
        pcc_allocator_lock_release()
        if ptr_is_null(user):
            return null()
        store_i64(user, -24, size)
        pcc_allocator_account_allocate(size, usable, 0)
        return user
    return pcc_allocator_allocate_raw(size, 16)


@c_abi_export("free")
def pcc_free(ptr) -> None:
    if ptr_is_null(ptr):
        return
    requested: i64 = load_i64(ptr, -24)
    usable: i64 = load_i64(ptr, -16)
    mapping_size: i64 = load_i64(ptr, -32)
    pcc_allocator_account_free(requested, usable)
    if mapping_size == 0:
        pcc_allocator_lock_acquire()
        pcc_allocator_put_small(ptr, usable)
        pcc_allocator_lock_release()
        return
    base = load_ptr(ptr, -40)
    if page_free(base, mapping_size) == 0:
        atomic_rmw_i64(
            "sub",
            global_addr("pcc_allocator_mapped"),
            0,
            mapping_size,
            "relaxed",
        )


@c_abi_export("malloc_usable_size")
def pcc_malloc_usable_size(ptr) -> i64:
    if ptr_is_null(ptr):
        return 0
    return load_i64(ptr, -16)


@c_abi_export("calloc")
def pcc_calloc(count: i64, size: i64) -> c_ptr:
    if count < 0 or size < 0:
        return null()
    if mul_overflow_i64(count, size):
        return null()
    total: i64 = wrapping_mul_i64(count, size)
    ptr = pcc_malloc(total)
    if ptr_is_null(ptr):
        return null()
    # Zero eight bytes per iteration.  This loop is the single hottest
    # instruction stream in a `pcc1 -> pcc2` build (~19% of samples on its
    # own): every managed object is allocated through it.  The allocator hands
    # back 16-byte aligned blocks, so the wide stores below are always
    # 8-byte aligned; the tail stays byte-wise.
    index: i64 = 0
    limit: i64 = total - 8
    while index <= limit:
        store_i64(ptr, index, 0)
        index = index + 8
    while index < total:
        store_i8(ptr, index, 0)
        index = index + 1
    return ptr


@c_abi_export("realloc")
def pcc_realloc(ptr, size: i64) -> c_ptr:
    if ptr_is_null(ptr):
        return pcc_malloc(size)
    if size == 0:
        pcc_free(ptr)
        return null()
    if size < 0:
        return null()
    usable: i64 = load_i64(ptr, -16)
    if size <= usable:
        old_size: i64 = load_i64(ptr, -24)
        store_i64(ptr, -24, size)
        atomic_rmw_i64(
            "add",
            global_addr("pcc_allocator_live_requested"),
            0,
            size - old_size,
            "relaxed",
        )
        return ptr

    alignment: i64 = load_i64(ptr, -8)
    if alignment == 16 and size <= 2048:
        replacement = pcc_malloc(size)
    else:
        replacement = pcc_allocator_allocate_raw(size, alignment)
    if ptr_is_null(replacement):
        return null()
    old_size = load_i64(ptr, -24)
    copy_size: i64 = old_size
    if copy_size > size:
        copy_size = size
    i: i64 = 0
    while i < copy_size:
        store_i8(replacement, i, load_i8(ptr, i))
        i = i + 1
    pcc_free(ptr)
    return replacement


@c_abi_export("memalign")
def pcc_memalign(alignment: i64, size: i64) -> c_ptr:
    if alignment < 8 or (alignment & (alignment - 1)) != 0:
        return null()
    if alignment <= 16:
        return pcc_malloc(size)
    return pcc_allocator_allocate_raw(size, alignment)


@c_abi_export("aligned_alloc")
def pcc_aligned_alloc(alignment: i64, size: i64) -> c_ptr:
    if (
        alignment < 8
        or (alignment & (alignment - 1)) != 0
        or size < 0
        or (size & (alignment - 1)) != 0
    ):
        return null()
    if alignment <= 16:
        return pcc_malloc(size)
    return pcc_allocator_allocate_raw(size, alignment)


@c_abi_export("posix_memalign")
def pcc_posix_memalign(out_ptr, alignment: i64, size: i64) -> i64:
    if alignment < 8 or (alignment & (alignment - 1)) != 0:
        return 22
    ptr = pcc_memalign(alignment, size)
    if ptr_is_null(ptr):
        return 12
    store_ptr(out_ptr, 0, ptr)
    return 0
