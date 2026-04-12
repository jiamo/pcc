"""pcc ``extern "C"`` FFI declarations (P6C.1 scaffold).

The self-hosting roadmap (``docs/plans/python-frontend-plan.md``
§Phase 6C.1) requires that compiled Python be able to declare and
call arbitrary C library entry points directly, without going
through CPython or any other interpreter. This module hosts the
type markers and the ``extern()`` factory that pcc's codegen will
recognize.

Runtime status (2026-04-20): the types here are plain Python classes
used by the frontend for static-analysis / type-inference. The
codegen recognition that lowers ``extern("libc_func")(x, y)`` into a
direct LLVM ``call`` has NOT yet landed — that is P6C.1's full
deliverable. The scaffold exists so user code can be written in the
intended shape today and compiled as soon as the codegen lands.

Example (forward-looking)::

    from pcc.extern import extern, c_int, c_str, c_ptr

    printf: extern = extern(
        "printf",
        argtypes=(c_str,),
        restype=c_int,
        variadic=True,
    )

    def main() -> None:
        printf(c_str("hello\\n"))

    main()

The eventual codegen recognizes the ``extern()`` factory result as
an opaque callable and lowers calls through it to a direct LLVM
``call @printf(...)`` with no Python-runtime trampoline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class _CType:
    """Opaque marker for a C scalar type. Instances map one-to-one
    onto an LLVM IR type via the P6C.1 codegen rules."""

    __slots__ = ("name", "ir_name")

    def __init__(self, name: str, ir_name: str) -> None:
        self.name = name
        self.ir_name = ir_name

    def __repr__(self) -> str:
        return f"<CType {self.name}>"


c_void = _CType("void", "void")
c_bool = _CType("bool", "i1")
c_int8 = _CType("int8", "i8")
c_int16 = _CType("int16", "i16")
c_int32 = _CType("int32", "i32")
c_int64 = _CType("int64", "i64")
c_uint8 = _CType("uint8", "i8")
c_uint16 = _CType("uint16", "i16")
c_uint32 = _CType("uint32", "i32")
c_uint64 = _CType("uint64", "i64")

# Convenience aliases matching typical platform sizes.
c_int = c_int32
c_long = c_int64
c_size_t = c_uint64

c_float = _CType("float", "float")
c_double = _CType("double", "double")

# Pointers are all opaque ``ptr`` at the IR level; the semantic
# distinction (c_str vs generic c_ptr) is tracked for the frontend's
# type-inference layer.
c_ptr = _CType("ptr", "ptr")
c_str = _CType("cstr", "ptr")


@dataclass(frozen=True)
class ExternFn:
    """Declaration of an ``extern "C"`` function. The frontend treats
    instances as callables at the AST level; codegen lowers each call
    to a direct ``call @<symbol>(...)``."""

    symbol: str
    argtypes: tuple[_CType, ...]
    restype: _CType = c_void
    variadic: bool = False
    # Optional lib name for library-load hints. None means the symbol
    # is expected to resolve at static-link time.
    lib: str | None = None

    def __call__(self) -> Any:
        """Runtime trap. The frontend rewrites each call to a direct
        LLVM ``call`` before this method would fire — it only
        executes on CPython (i.e. during pcc's own interpreter-mode
        development) where we fall through to a NotImplementedError
        so misuse is loud.

        Kept arg-less on purpose: a ``*args`` signature would be a
        self-host audit blocker (pcc frontend doesn't lower variadic
        defs). The frontend lowers every extern call site before it
        ever reaches this method, so the runtime signature is moot —
        any caller that actually gets here is already going to
        ``raise``. The AttributeError / TypeError from argument-count
        mismatch lands at the same point as the NotImplementedError
        would have."""
        raise NotImplementedError(
            f"extern({self.symbol!r}) called from interpreted Python — "
            "this call site should have been lowered at compile time"
        )


def extern(
    symbol: str,
    argtypes: tuple[_CType, ...] = (),
    restype: _CType = c_void,
    variadic: bool = False,
    lib: str | None = None,
) -> ExternFn:
    """Factory for an extern-C function declaration.

    Instances are consumed by pcc's codegen via isinstance checks in
    the type-inference layer. Using :class:`ExternFn` as a call
    target triggers the direct-call lowering in P6C.1."""
    return ExternFn(
        symbol=symbol, argtypes=argtypes, restype=restype,
        variadic=variadic, lib=lib,
    )


__all__ = [
    "ExternFn",
    "extern",
    "c_void", "c_bool",
    "c_int8", "c_int16", "c_int32", "c_int64",
    "c_uint8", "c_uint16", "c_uint32", "c_uint64",
    "c_int", "c_long", "c_size_t",
    "c_float", "c_double",
    "c_ptr", "c_str",
]
