"""Phase 4c.3: pcc-Python port of py_exc_tls.c.

Reimplements the four TLS-facing exception entries:
    py_raise, py_err_occurred, py_current_exception, py_clear_exception

Plus the raw-slot accessors py_tls_exc_get / py_tls_exc_set stay in
their cc-compiled .o (they manage the actual _Thread_local slot); the
Python port uses them via extern.

PyExceptionObject layout (from py_internal.h):
    offset  0   PyObjectHeader  (i64 refcount, i32 tag, i32 flags — 16 bytes total)
    offset  16  exc_class  (ptr)
    offset  24  message    (ptr)
    offset  32  cause      (ptr)
    offset  40  context    (ptr)
    ...

Tagged-int encoding: low bit of pointer == 1 means tagged int. Non-
tagged pointers come from malloc (8-byte aligned) so low bit is 0.

PY_TYPE_EXC == 12 (py_internal.h).
PY_EXC_RUNTIMEERROR == 7 (py_internal.h).
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.unsafe import (
    cstr,
    is_tagged_int,
    load_i32,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
    store_ptr,
)


py_tls_exc_get         = extern("py_tls_exc_get",         (),        c_ptr)
py_tls_exc_set         = extern("py_tls_exc_set",         (c_ptr,),  c_void)
py_incref              = extern("py_incref",              (c_ptr,),  c_void)
py_decref              = extern("py_decref",              (c_ptr,),  c_void)
py_exc_new             = extern("py_exc_new",             (c_int64, c_ptr), c_ptr)
py_exc_new_with_class  = extern("py_exc_new_with_class",  (c_ptr, c_ptr), c_ptr)
py_exc_builtin_class   = extern("py_exc_builtin_class",   (c_int64,), c_ptr)
py_isinstance          = extern("py_isinstance",          (c_ptr, c_ptr), c_int64)
py_instance_getattr    = extern("py_instance_getattr",    (c_ptr, c_ptr), c_ptr)
py_obj_str             = extern("py_obj_str",             (c_ptr,), c_ptr)
py_str_utf8            = extern("py_str_utf8",            (c_ptr,), c_ptr)


def _type_of(obj) -> int:
    # Offsets / tag literals inlined to avoid module-level globals
    # (which would require a main() for init). See py_obj_stubs.py.
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)   # PyObjectHeader.type_tag


def _instance_like(obj) -> int:
    if ptr_is_null(obj):
        return 0
    if is_tagged_int(obj):
        return 0
    tag: int = _type_of(obj)
    if tag == 11:             # PY_TYPE_INSTANCE
        return 1
    if tag >= 100:            # PY_TYPE_USER
        return 1
    return 0


def _normalize_raised(exc):
    # Returns a reference owned by the exception slot. Existing
    # PyExceptionObject values are incref'd; freshly normalized errors
    # already carry refcount=1 from py_exc_new*.
    if ptr_is_null(exc):
        return py_exc_new(
            7,  # PY_EXC_RUNTIMEERROR
            cstr("no active exception to reraise"),
        )
    if _type_of(exc) == 12:              # PY_TYPE_EXC
        py_incref(exc)
        return exc

    base = py_exc_builtin_class(0)       # PY_EXC_BASE
    if not ptr_is_null(base):
        if py_isinstance(exc, base) != 0:
            cls = null()
            if _instance_like(exc) != 0:
                cls = load_ptr(exc, 16)

            msg_c = null()
            msg_attr = null()
            msg_str = null()
            if _instance_like(exc) != 0:
                msg_attr = py_instance_getattr(exc, cstr("message"))
                if not ptr_is_null(msg_attr):
                    if _type_of(msg_attr) == 4:       # PY_TYPE_STR
                        msg_c = py_str_utf8(msg_attr)
                    else:
                        msg_str = py_obj_str(msg_attr)
                        if not ptr_is_null(msg_str):
                            msg_c = py_str_utf8(msg_str)
            if ptr_is_null(msg_c):
                msg_str = py_obj_str(exc)
                if not ptr_is_null(msg_str):
                    msg_c = py_str_utf8(msg_str)

            normalized = py_exc_new_with_class(cls, msg_c)
            if not ptr_is_null(msg_str):
                py_decref(msg_str)
            if not ptr_is_null(msg_attr):
                py_decref(msg_attr)
            if not ptr_is_null(normalized):
                return normalized

    return py_exc_new(
        3,  # PY_EXC_TYPEERROR
        cstr("exceptions must derive from BaseException"),
    )


@c_abi_export("py_err_occurred")
def py_err_occurred() -> int:
    cur = py_tls_exc_get()
    if ptr_is_null(cur):
        return 0
    return 1


@c_abi_export("py_current_exception")
def py_current_exception():
    # Borrowed reference — TLS still owns it.
    return py_tls_exc_get()


@c_abi_export("py_clear_exception")
def py_clear_exception() -> None:
    cur = py_tls_exc_get()
    if not ptr_is_null(cur):
        py_decref(cur)
        py_tls_exc_set(null())


@c_abi_export("py_raise")
def py_raise(exc) -> None:
    exc = _normalize_raised(exc)
    cur = py_tls_exc_get()
    cur_is_null: bool = ptr_is_null(cur)
    exc_is_null: bool = ptr_is_null(exc)

    # Auto-chain implicit __context__ when replacing a pending
    # exception during except-block handling. Matches CPython.
    if not cur_is_null and not exc_is_null:
        if ptr_eq(cur, exc) == 0:
            tag: int = _type_of(exc)
            if tag == 12:                     # PY_TYPE_EXC
                existing_ctx = load_ptr(exc, 40)   # offset of ->context
                if ptr_is_null(existing_ctx):
                    py_incref(cur)
                    store_ptr(exc, 40, cur)

    if not cur_is_null:
        py_decref(cur)
    py_tls_exc_set(exc)
    # Caller propagates via post-call py_err_occurred() check.
