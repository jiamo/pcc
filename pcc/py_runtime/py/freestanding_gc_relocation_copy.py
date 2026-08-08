"""Backend 4 single-use relocation copy transaction."""
from pcc import i64
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_MEMORYVIEW,
)

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    global_addr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    memmove,
    null,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_gc_alloc = extern(
    "pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr
)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_backend4_evacuation_page_remove = extern(
    "pcc_gc_backend4_evacuation_page_remove", (c_ptr,), c_void
)
pcc_gc_backend4_relocate_copy_supported_tag = extern(
    "pcc_gc_backend4_relocate_copy_supported_tag", (c_int64,), c_int64
)
pcc_gc_backend4_relocation_set_contains_page = extern(
    "pcc_gc_backend4_relocation_set_contains_page", (c_ptr,), c_int64
)
pcc_gc_backend4_relocation_set_find = extern(
    "pcc_gc_backend4_relocation_set_find", (c_ptr,), c_ptr
)
pcc_gc_backend4_relocation_set_remove = extern(
    "pcc_gc_backend4_relocation_set_remove", (c_ptr,), c_void
)
pcc_gc_backend4_zpage_page_for_owner = extern(
    "pcc_gc_backend4_zpage_page_for_owner", (c_ptr,), c_ptr
)
pcc_gc_backend4_zpage_remove = extern(
    "pcc_gc_backend4_zpage_remove", (c_ptr,), c_void
)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_gc_forwarding_find = extern("pcc_gc_forwarding_find", (c_ptr,), c_ptr)
pcc_gc_install_forwarding_unlocked = extern(
    "pcc_gc_install_forwarding_unlocked", (c_ptr, c_ptr), c_int64
)
pcc_gc_object_known_size = extern(
    "pcc_gc_object_known_size", (c_ptr,), c_int64
)
pcc_gc_relocate_copy_payload = extern(
    "pcc_gc_relocate_copy_payload", (c_ptr, c_ptr, c_int64, c_int64), c_int64
)
pcc_gc_memoryview_refresh_owned_buffer = extern(
    "pcc_gc_memoryview_refresh_owned_buffer", (c_ptr,), c_int64
)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)


@c_abi_export("pcc_gc_backend4_relocate_copy_unlocked")
def pcc_gc_backend4_relocate_copy_unlocked(from_obj, size: i64):
    if pcc_gc_backend() != 4:
        return null()
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return null()
    if size < 16:
        return null()
    if ptr_is_null(pcc_gc_forwarding_find(from_obj)) == 0:
        return null()
    if ptr_is_null(pcc_gc_backend4_relocation_set_find(from_obj)) != 0:
        return null()
    flags: i64 = load_i32(from_obj, 12)
    if (flags & 64) != 0:
        return null()
    tag: i64 = load_i32(from_obj, 8)
    if pcc_gc_backend4_relocate_copy_supported_tag(tag) == 0:
        return null()
    known_size: i64 = pcc_gc_object_known_size(from_obj)
    if known_size <= 0 or size > known_size:
        return null()
    to_obj = pcc_gc_alloc(size, tag, flags & ~10240)
    if ptr_is_null(to_obj) != 0:
        return null()
    # The header copy clobbers allocation-origin flags.  Preserve the
    # destination residency so chained relocation cannot undercount the page
    # that physically owns the replacement object.
    to_residency: i64 = load_i32(to_obj, 12) & 331776
    memmove(to_obj, from_obj, size)
    store_i64(to_obj, 0, 1)
    new_flags: i64 = load_i32(to_obj, 12)
    store_i32(to_obj, 12, (new_flags & ~342016) | to_residency)
    if pcc_gc_relocate_copy_payload(from_obj, to_obj, tag, size) == 0:
        py_decref(to_obj)
        return null()
    if pcc_gc_install_forwarding_unlocked(from_obj, to_obj) != 0:
        py_decref(to_obj)
        return null()
    if tag == PY_TYPE_MEMORYVIEW:  # PY_TYPE_MEMORYVIEW
        # Commit the raw-allocation ownership transfer only after forwarding
        # itself cannot fail.  The payload phase deliberately left to_obj's
        # field NULL so all preceding rollback paths remain dealloc-safe.
        owned_buffer = load_ptr(from_obj, 24)
        store_ptr(to_obj, 24, owned_buffer)
        store_ptr(from_obj, 24, null())
        pcc_gc_memoryview_refresh_owned_buffer(to_obj)
    # Count-on-NEW: move the source copy's complete outstanding count onto the
    # replacement and leave the source as an immortal forwarding shell until
    # page retirement after a later remap epoch.
    outstanding: i64 = load_i64(from_obj, 0)
    if outstanding > 0:
        store_i64(to_obj, 0, load_i64(to_obj, 0) + outstanding)
    store_i32(from_obj, 12, load_i32(from_obj, 12) | 1)
    from_page = pcc_gc_backend4_zpage_page_for_owner(from_obj)
    evacuated: i64 = load_i32(
        global_addr("pcc_gc_backend4_evacuated_bytes_count"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_evacuated_bytes_count"),
        0,
        evacuated + size,
    )
    pcc_gc_backend4_relocation_set_remove(from_obj)
    if ptr_is_null(from_page) == 0:
        if pcc_gc_backend4_relocation_set_contains_page(from_page) == 0:
            pcc_gc_backend4_evacuation_page_remove(from_page)
    pcc_gc_backend4_zpage_remove(from_obj)
    return to_obj


@c_abi_export("pcc_gc_relocate_copy")
def pcc_gc_relocate_copy(from_obj, size: i64):
    pcc_gc_config_ensure()
    pcc_py_gc_minor_graph_lock()
    to_obj = pcc_gc_backend4_relocate_copy_unlocked(from_obj, size)
    pcc_py_gc_minor_graph_unlock()
    return to_obj
