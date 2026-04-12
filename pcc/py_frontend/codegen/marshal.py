"""Marshalling helpers between native scalars and PyObject*.

Phase 2 of the Python frontend splits any expression into an L1 "native"
and an L2 "boxed PyObject*" form. Whenever a native-typed value has to
interoperate with a container element, a runtime-lib call, or a mixed-
arity print, we *marshal* it into ``PyObject*`` via :func:`marshal_to_object`.
The reverse direction — unpacking a ``PyObject*`` back into a native
scalar — lives in :func:`marshal_from_object`.

These helpers operate in terms of:

* an ``ir.IRBuilder`` at a valid insertion point,
* a ``runtime`` dict mapping runtime-lib names to ``ir.Function`` (same
  shape as :func:`pcc.py_frontend.codegen.runtime_abi.declare_runtime`
  returns),
* the source ``pcc_py`` :class:`Type`, and
* the target type description (either "PyObject*" for to_object, or a
  destination :class:`Type` for from_object).

They never mutate the IR module — all declarations are expected to have
been set up by :func:`declare_runtime` beforehand.

Mirrors the ABI documented in Section 3 of
``docs/plans/python-frontend-interfaces.md``.
"""
from __future__ import annotations

from typing import Mapping

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    BoolType,
    DictType,
    FloatType,
    IntType,
    ListType,
    NoneType,
    StrType,
    TupleType,
    Type,
)
from .runtime_abi import declare_runtime_global


# -- Canonical IR types ------------------------------------------------------

_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
_PTR = _I8.as_pointer()   # PyObject*


def _tmp(builder: ir.IRBuilder, hint: str) -> str:
    """Make a unique SSA name by salting with the builder's block id."""
    return f"{hint}.{id(builder.block):x}"


def is_object_type(ty: Type) -> bool:
    """True if ``ty`` must be represented as ``PyObject*`` at runtime."""
    return isinstance(ty, (StrType, ListType, DictType, TupleType, NoneType))


def is_native_type(ty: Type) -> bool:
    """True if ``ty`` has a native LLVM-IR scalar representation."""
    return isinstance(ty, (IntType, FloatType, BoolType))


def marshal_to_object(
    builder: ir.IRBuilder,
    module: ir.Module,
    runtime: Mapping[str, ir.Function],
    value: ir.Value,
    ty: Type,
) -> ir.Value:
    """Return a ``PyObject*`` representation of ``value`` (typed ``ty``).

    * ``int``   → ``py_int_from_i64``
    * ``float`` → ``py_float_from_f64``
    * ``bool``  → ``py_bool_from_bit`` (with a zext to i32)
    * ``None``  → the global ``py_None`` (borrowed, INCREF'd by runtime
      on use; we return the const pointer here)
    * ``str`` / ``list`` / ``dict`` / ``tuple`` → already a ``PyObject*``,
      pass through.

    Any other type raises :class:`NotImplementedError`; the L3 layer
    picks those up.
    """
    if isinstance(ty, IntType):
        # bool backed as i1 also comes through here when common_type
        # coerces to int; guard width.
        v64 = value
        if value.type is not _I64:
            if isinstance(value.type, ir.IntType):
                if value.type.width == 64:
                    v64 = value
                else:
                    v64 = builder.sext(value, _I64, name="m.b2i64")
            elif isinstance(value.type, ir.PointerType):
                # int-annotated but held as PyObject* — the value came
                # from a CPython / dyn path that boxed it. Already a
                # PyObject*, pass through.
                return value
            else:
                raise NotImplementedError(
                    f"marshal_to_object: unexpected IR type {value.type} for int"
                )
        return builder.call(runtime["py_int_from_i64"], [v64],
                              name="m.int_box")
    if isinstance(ty, FloatType):
        return builder.call(runtime["py_float_from_f64"], [value],
                              name="m.flt_box")
    if isinstance(ty, BoolType):
        # py_bool_from_bit takes i32.
        if value.type is _I1:
            bit = builder.zext(value, _I32, name="m.b2i32")
        elif isinstance(value.type, ir.IntType):
            if value.type.width == 32:
                bit = value
            elif value.type.width > 32:
                bit = builder.trunc(value, _I32, name="m.b2i32")
            else:
                bit = builder.zext(value, _I32, name="m.b2i32")
        else:
            raise NotImplementedError(
                f"marshal_to_object: unexpected IR type {value.type} for bool"
            )
        return builder.call(runtime["py_bool_from_bit"], [bit],
                              name="m.bool_box")
    if isinstance(ty, NoneType):
        # If caller already has a pointer (e.g. loaded py_None earlier),
        # return it; otherwise materialise a load of the global.
        if isinstance(value.type, ir.PointerType):
            return value
        gv = declare_runtime_global(module, "py_None")
        return builder.load(gv, name="m.none")
    if isinstance(ty, (StrType, ListType, DictType, TupleType)):
        # Already a PyObject*; the caller is responsible for having a
        # pointer-typed value at this point.
        return value
    # DynType values are usually ``PyObject*`` at the IR level (class
    # instances, attribute loads, results of ``MyClass(args)``). But if
    # an earlier coercion already unboxed them to a native scalar (e.g.
    # an ``int + 1`` on a DynType attribute that went through
    # ``py_int_to_i64``), marshal back to ``PyObject*`` based on the
    # LLVM type we actually hold.
    from ..py_ast import DynType  # local import to avoid cycle
    if isinstance(ty, DynType):
        if isinstance(value.type, ir.PointerType):
            return value
        if value.type is _I64:
            return builder.call(runtime["py_int_from_i64"], [value],
                                  name="m.dyn.int_box")
        if isinstance(value.type, ir.IntType):
            if value.type.width == 1:
                bit = builder.zext(value, _I32, name="m.dyn.b2i32")
                return builder.call(runtime["py_bool_from_bit"], [bit],
                                      name="m.dyn.bool_box")
            widened = builder.sext(value, _I64, name="m.dyn.sext64")
            return builder.call(runtime["py_int_from_i64"], [widened],
                                  name="m.dyn.int_box")
        if value.type is ir.DoubleType():
            return builder.call(runtime["py_float_from_f64"], [value],
                                  name="m.dyn.flt_box")
        raise NotImplementedError(
            f"marshal_to_object: DynType with IR {value.type} not supported"
        )
    raise NotImplementedError(
        f"marshal_to_object: type {type(ty).__name__} not supported"
    )


def marshal_from_object(
    builder: ir.IRBuilder,
    module: ir.Module,
    runtime: Mapping[str, ir.Function],
    pyobj: ir.Value,
    target_ty: Type,
) -> ir.Value:
    """Unpack a ``PyObject*`` into the native representation of ``target_ty``.

    * ``int``   → ``py_int_to_i64`` (overflow flag discarded at L1/L2 for
      now; Phase 3 propagates it to an exception).
    * ``float`` → ``py_float_to_f64``
    * ``bool``  → ``py_obj_truthy`` truncated to i1.
    * ``str`` / ``list`` / ``dict`` / ``tuple`` / ``None`` → pass through
      (they already live as ``PyObject*`` natively).
    """
    if isinstance(target_ty, IntType):
        # Allocate an overflow flag slot; caller can ignore.
        ov_slot = _stash_overflow_slot(builder)
        return builder.call(
            runtime["py_int_to_i64"], [pyobj, ov_slot], name="m.int_unbox"
        )
    if isinstance(target_ty, FloatType):
        return builder.call(
            runtime["py_float_to_f64"], [pyobj], name="m.flt_unbox"
        )
    if isinstance(target_ty, BoolType):
        i32 = builder.call(
            runtime["py_obj_truthy"], [pyobj], name="m.bool_unbox_i32"
        )
        return builder.trunc(i32, _I1, name="m.bool_unbox")
    if isinstance(target_ty, (StrType, ListType, DictType, TupleType, NoneType)):
        return pyobj
    raise NotImplementedError(
        f"marshal_from_object: target {type(target_ty).__name__} not supported"
    )


def _stash_overflow_slot(builder: ir.IRBuilder) -> ir.Value:
    """Allocate an int32 stack slot to receive an overflow flag.

    The slot is left uninitialised — :func:`py_int_to_i64` writes it
    unconditionally. We hoist it to the entry block so repeated use in a
    loop body does not grow the stack.

    NOTE: llvmlite's IRBuilder position is an integer ``_anchor`` into
    the block's instruction list. Inserting via a separate builder at
    the top of the entry block *does not* shift the primary builder's
    anchor, so subsequent emissions on the primary builder land at the
    wrong index and orphan previously-emitted instructions behind the
    terminator. We therefore temporarily repoint the caller's own
    builder to the allocation slot, emit, and restore the anchor.
    """
    fn = builder.block.function
    entry = fn.entry_basic_block
    insert_before = None
    for instr in entry.instructions:
        if instr.opname != "alloca":
            insert_before = instr
            break

    saved_block = builder.block
    saved_anchor = builder._anchor

    if insert_before is not None:
        builder.position_before(insert_before)
    else:
        builder.position_at_end(entry)
    slot = builder.alloca(_I32, name="ov.flag")

    if saved_block is not None:
        builder._block = saved_block
        # If we inserted into the same block, the new alloca shifted
        # subsequent indices by 1 — account for that so future inserts
        # land where the caller expected.
        if saved_block is entry and insert_before is not None:
            builder._anchor = saved_anchor + 1
        else:
            builder._anchor = saved_anchor
    return slot


__all__ = [
    "is_object_type",
    "is_native_type",
    "marshal_to_object",
    "marshal_from_object",
]
