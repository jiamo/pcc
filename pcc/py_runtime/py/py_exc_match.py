"""Phase 4c.6a: pcc-Python port of py_exc_match.c.

Ports py_exc_matches — the hot MRO-aware class matcher used by every
try/except handler dispatch. Traceback frame growth and unhandled
stderr formatting live in sibling py_exc_traceback.py.

PyExceptionObject layout (offset 16 -> exc_class).

Public object type tags and class/instance layout offsets come from the
generated ``py_abi_constants`` module.
"""

__pcc_runtime_port__ = True

from pcc.py_runtime.py.py_abi_constants import (
    PYCLASSOBJECT_MRO_OFFSET,
    PYINSTANCEOBJECT_CLS_OFFSET,
    PY_TYPE_CLASS,
    PY_TYPE_EXC,
    PY_TYPE_INSTANCE,
    PY_TYPE_INT,
    PY_TYPE_USER_CLASS_START,
)
from pcc.extern import c_abi_export, c_ptr, extern
from pcc.unsafe import (
    is_tagged_int,
    load_i32,
    load_ptr,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
)

pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_note_relocation_read = extern("pcc_gc_note_relocation_read", (c_ptr,), c_ptr)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return PY_TYPE_INT
    return load_i32(obj, 8)


def _to_class(obj):
    """Project a PyObject* down to its PyClassObject* form.
    Returns NULL if not usable as a class key."""
    if ptr_is_null(obj):
        return null()
    if is_tagged_int(obj):
        return null()
    obj = pcc_gc_note_relocation_read(obj)
    tag: int = load_i32(obj, 8)
    if tag == PY_TYPE_CLASS:                         # PY_TYPE_CLASS
        return obj
    if tag == PY_TYPE_EXC:                         # PY_TYPE_EXC
        return pcc_gc_load_ptr(obj, ptr_add(obj, 16))   # ->exc_class
    if tag == PY_TYPE_INSTANCE or tag >= PY_TYPE_USER_CLASS_START:
        # A raised user exception subclass is a PyInstanceObject; its ``cls``
        # is at offset 16 (right after the 16-byte header), same slot as
        # PyExceptionObject->exc_class. Project to it so the MRO walk matches
        # ``except MyError`` / ``except Exception``.
        return pcc_gc_load_ptr(
            obj, ptr_add(obj, PYINSTANCEOBJECT_CLS_OFFSET)
        )
    return null()


@c_abi_export("py_exc_matches")
def py_exc_matches(exc, type_) -> int:
    ecls = _to_class(exc)
    tcls = _to_class(type_)
    if ptr_is_null(ecls):
        return 0
    if ptr_is_null(tcls):
        return 0
    mro = load_ptr(ecls, PYCLASSOBJECT_MRO_OFFSET)
    if ptr_is_null(mro):
        if ptr_eq(ecls, tcls):
            return 1
        return 0
    n_mro: int = load_i32(ecls, 40)
    i: int = 0
    while i < n_mro:
        entry = pcc_gc_load_ptr(ecls, ptr_add(mro, i * 8))
        if ptr_eq(entry, tcls):
            return 1
        i = i + 1
    return 0
