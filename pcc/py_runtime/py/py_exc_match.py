"""Phase 4c.6a: pcc-Python port of py_exc_match.c.

Ports py_exc_matches — the hot MRO-aware class matcher used by every
try/except handler dispatch. Traceback frame growth and unhandled
stderr formatting live in sibling py_exc_traceback.py.

PyClassObject layout (from py_internal.h, LP64 with natural alignment):
    offset   0   PyObjectHeader          (16 bytes)
    offset  16   name                    (ptr)
    offset  24   n_bases                 (i32 + 4 pad)
    offset  32   bases                   (ptr)
    offset  40   n_mro                   (i32 + 4 pad)
    offset  48   mro                     (ptr to PyClassObject* array)
    offset  56   n_methods               (i32 + 4 pad)
    offset  64   methods                 (ptr)
    offset  72   n_fields                (i32 + 4 pad)
    offset  80   field_names             (ptr)
    offset  88   instance_size           (i32)
    offset  92   type_tag_alloc          (i32)

PyExceptionObject layout (offset 16 -> exc_class).

Constants: PY_TYPE_CLASS = 10, PY_TYPE_EXC = 12.
"""
from pcc.extern import c_abi_export
from pcc.unsafe import (
    is_tagged_int,
    load_i32,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2
    return load_i32(obj, 8)


def _to_class(obj):
    """Project a PyObject* down to its PyClassObject* form.
    Returns NULL if not usable as a class key."""
    if ptr_is_null(obj):
        return null()
    if is_tagged_int(obj):
        return null()
    tag: int = load_i32(obj, 8)
    if tag == 10:                         # PY_TYPE_CLASS
        return obj
    if tag == 12:                         # PY_TYPE_EXC
        return load_ptr(obj, 16)   # ->exc_class
    return null()


@c_abi_export("py_exc_matches")
def py_exc_matches(exc, type_) -> int:
    ecls = _to_class(exc)
    tcls = _to_class(type_)
    if ptr_is_null(ecls):
        return 0
    if ptr_is_null(tcls):
        return 0
    mro = load_ptr(ecls, 48)     # PyClassObject->mro
    if ptr_is_null(mro):
        if ptr_eq(ecls, tcls):
            return 1
        return 0
    n_mro: int = load_i32(ecls, 40)
    i: int = 0
    while i < n_mro:
        entry = load_ptr(mro, i * 8)
        if ptr_eq(entry, tcls):
            return 1
        i = i + 1
    return 0
