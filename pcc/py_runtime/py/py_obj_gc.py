"""Phase 4c.7: pcc-Python port of py_obj_gc.c.

Four GC stubs — py_gc_init, py_gc_collect, py_gc_track,
py_gc_untrack. The ABI requires the symbols; semantics are no-ops
until a real tricolor collector lands.

Flag bit for GC-tracked lives at offset 12 (PyObjectHeader.flags).
PY_FLAG_GC_TRACKED = 0x2.
"""
from pcc.extern import c_abi_export
from pcc.unsafe import is_tagged_int, load_i32, ptr_is_null, store_i32


@c_abi_export("py_gc_init")
def py_gc_init() -> None:
    # TODO(phase2+): init tri-color lists
    return


@c_abi_export("py_gc_collect")
def py_gc_collect() -> None:
    # TODO(phase2+): run a collection
    return


@c_abi_export("py_gc_track")
def py_gc_track(o) -> None:
    if ptr_is_null(o):
        return
    if is_tagged_int(o):
        return
    flags: int = load_i32(o, 12)
    store_i32(o, 12, flags | 2)     # |= PY_FLAG_GC_TRACKED


@c_abi_export("py_gc_untrack")
def py_gc_untrack(o) -> None:
    if ptr_is_null(o):
        return
    if is_tagged_int(o):
        return
    flags: int = load_i32(o, 12)
    store_i32(o, 12, flags & ~2)    # &= ~PY_FLAG_GC_TRACKED
