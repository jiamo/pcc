"""PCC Kernel IR — the kernel-ONLY IR boundary (row K-P0-TVM-KERNEL-IR).

This is the narrowest IR in pcc: it may carry only what a GPU/tile kernel is
legally allowed to see. Concretely:

  * POD scalar parameters (i8/i16/i32/i64/f16/f32/f64/bool, unsigned variants,
    and pcc.i64/pcc.u64)
  * buffer-handle parameters (opaque device/host buffer views, NOT PyObject*)
  * layout metadata (row-major / col-major / an opaque named TileLayout)
  * thread / block binding + memory scope (global/shared/fragment/local)
  * fence / barrier tokens

It EXPLICITLY REJECTS every host escape: Python ``list`` / ``dict`` / arbitrary
``PyObject`` references / weakrefs / finalizers (``__del__``) / GC-frame
escapes. ``validate_kernel`` RAISES on any of these — that raise IS the
enforcement of the hard claim boundary "device IR never sees a GC-managed
PyObject" (see docs/design/pcc-kernel-ir.md §4).

The default helpers are metadata/golden-oriented, but the IR is also consumed by
the Metal artifact proof path. Device-local buffers are modeled separately from
host-visible parameters so threadgroup/local storage cannot be mistaken for a
CPU host-launch argument.

Importable standalone::

    from pcc.kernel_ir.ir import KernelModule, validate_kernel, dump_kernel
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Any


class KernelIRError(ValueError):
    """A kernel IR construct violated the kernel-only boundary."""


class ScalarType(enum.Enum):
    """POD scalar types a kernel parameter/value may carry.

    ``INT`` here is the *value-projected* small-int lane meaning of Python
    ``int`` (see AGENTS.md obligation 7): a kernel scalar is a proven-in-range
    machine scalar, never a boxed bignum. Arbitrary-precision int cannot cross
    the kernel frontier — that would be a boxed PyObject, which the validator
    rejects.
    """

    BOOL = "bool"
    I8 = "i8"
    U8 = "u8"
    I16 = "i16"
    U16 = "u16"
    I32 = "i32"
    I64 = "i64"
    U32 = "u32"
    U64 = "u64"
    F16 = "f16"
    F32 = "f32"
    F64 = "f64"


class MemoryScope(enum.Enum):
    """Memory scope a buffer lives in. Written into the IR, never guessed at
    codegen time (research report: scope + sync must be in the kernel IR)."""

    GLOBAL = "global"
    SHARED = "shared"
    FRAGMENT = "fragment"
    LOCAL = "local"


class Layout(enum.Enum):
    """Buffer layout. ``TILE`` is an opaque named TileLayout resolved later by
    the layout applier; it is a first-class citizen, not decoration."""

    ROW_MAJOR = "row_major"
    COL_MAJOR = "col_major"
    TILE = "tile"
    SWIZZLED = "swizzled"


# Type tags that a kernel parameter may NEVER carry. Kept as strings so this
# module has no dependency on the pcc runtime object model.
_REJECTED_PARAM_KINDS = frozenset(
    {
        "list",
        "dict",
        "set",
        "tuple",
        "pyobject",
        "object",
        "weakref",
        "finalizer",
        "gc_frame",
        "generator",
        "coroutine",
    }
)


@dataclass(frozen=True)
class ScalarParam:
    """A POD scalar kernel parameter."""

    name: str
    dtype: ScalarType

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "scalar", "name": self.name, "dtype": self.dtype.value}


@dataclass(frozen=True)
class BufferParam:
    """A buffer-handle kernel parameter.

    ``rank`` + ``dtype`` + ``scope`` + ``layout`` are static metadata. The
    buffer itself is an opaque handle at runtime (see hmm_fence.PccBufferHandle);
    the kernel never receives the address of a GC-managed object.
    """

    name: str
    dtype: ScalarType
    rank: int
    shape: tuple[int, ...] | None = None
    scope: MemoryScope = MemoryScope.GLOBAL
    layout: Layout = Layout.ROW_MAJOR

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": "buffer",
            "name": self.name,
            "dtype": self.dtype.value,
            "rank": self.rank,
            "scope": self.scope.value,
            "layout": self.layout.value,
        }
        if self.shape is not None:
            data["shape"] = list(self.shape)
        return data


@dataclass(frozen=True)
class LocalBuffer:
    """A device-local buffer allocated inside one kernel function.

    This is not a host launch parameter. ``scope`` must be a device-local scope:
    shared/threadgroup, fragment, or local. ``shape`` is static so source
    emitters and TIR-shape oracles can prove a bounded allocation.
    """

    name: str
    dtype: ScalarType
    shape: tuple[int, ...]
    scope: MemoryScope
    layout: Layout = Layout.ROW_MAJOR

    @property
    def rank(self) -> int:
        return len(self.shape)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "local_buffer",
            "name": self.name,
            "dtype": self.dtype.value,
            "rank": self.rank,
            "shape": list(self.shape),
            "scope": self.scope.value,
            "layout": self.layout.value,
        }


# A kernel body, in this first slice, is a flat list of tile-primitive ops
# represented as records. Real lowering is done by tirx_adapter; here we only
# model enough structure to validate escapes and to dump a golden.
@dataclass(frozen=True)
class KernelOp:
    """One tile-level operation in the kernel body.

    ``op`` is one of the accepted primitive names (copy/atomic_add/fill/gemm/
    gemm_sp/parallel/barrier/fence/elementwise_add/swizzle/layout_annotation)
    or the structured scalar/indexed subset used by ``@gpu.kernel``.
    ``args`` reference parameter/local names (strings) only — never live Python
    objects.
    """

    op: str
    args: tuple[str, ...] = ()
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "args": list(self.args), "attrs": dict(self.attrs)}


_ACCEPTED_OPS = frozenset(
    {
        "copy",
        "atomic_add",
        "copy_async",
        "fill",
        "gemm",
        "gemm_sp",
        "reduce",
        "parallel",
        "barrier",
        "fence",
        "elementwise_add",
        "scalar_assign",
        "indexed_store",
        "if_begin",
        "else",
        "if_end",
        "swizzle",
        "layout_annotation",
    }
)


@dataclass(frozen=True)
class KernelFunc:
    """A single kernel function: host params, device locals, and tile ops."""

    name: str
    params: tuple[Any, ...] = ()
    locals: tuple[LocalBuffer, ...] = ()
    body: tuple[KernelOp, ...] = ()
    grid: tuple[int, ...] = ()
    threads: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "grid": list(self.grid),
            "threads": self.threads,
            "params": [p.to_dict() for p in self.params],
            "locals": [l.to_dict() for l in self.locals],
            "body": [op.to_dict() for op in self.body],
        }


@dataclass(frozen=True)
class KernelModule:
    """A kernel-only IR module — the stable boundary between pcc HIR and the
    TIRx-compatible lowering layer."""

    name: str
    funcs: tuple[KernelFunc, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.name,
            "kernel_ir_version": 1,
            "funcs": [f.to_dict() for f in self.funcs],
        }


def _validate_param(param: Any, *, func_name: str) -> None:
    if isinstance(param, ScalarParam):
        if not isinstance(param.dtype, ScalarType):
            raise KernelIRError(
                f"kernel {func_name!r} param {param.name!r}: bad scalar dtype {param.dtype!r}"
            )
        return
    if isinstance(param, BufferParam):
        if not isinstance(param.dtype, ScalarType):
            raise KernelIRError(
                f"kernel {func_name!r} buffer {param.name!r}: bad element dtype {param.dtype!r}"
            )
        if param.rank < 0:
            raise KernelIRError(
                f"kernel {func_name!r} buffer {param.name!r}: negative rank {param.rank}"
            )
        if param.shape is not None:
            if len(param.shape) != param.rank:
                raise KernelIRError(
                    f"kernel {func_name!r} buffer {param.name!r}: shape rank "
                    f"{len(param.shape)} does not match rank {param.rank}"
                )
            if any((not isinstance(dim, int)) or dim <= 0 for dim in param.shape):
                raise KernelIRError(
                    f"kernel {func_name!r} buffer {param.name!r}: bad shape "
                    f"{param.shape!r}"
                )
        if not isinstance(param.scope, MemoryScope):
            raise KernelIRError(
                f"kernel {func_name!r} buffer {param.name!r}: bad scope {param.scope!r}"
            )
        if not isinstance(param.layout, Layout):
            raise KernelIRError(
                f"kernel {func_name!r} buffer {param.name!r}: bad layout {param.layout!r}"
            )
        return

    # Anything else is a host escape. Name the specific escape kind if we can.
    kind = _classify_escape(param)
    raise KernelIRError(
        f"kernel {func_name!r}: parameter {param!r} is a host escape "
        f"({kind}); the kernel IR only accepts POD scalar + buffer handle "
        f"parameters. list/dict/PyObject/weakref/finalizer/GC-frame are rejected."
    )


def _validate_local(local: Any, *, func_name: str) -> None:
    if not isinstance(local, LocalBuffer):
        kind = _classify_escape(local)
        raise KernelIRError(
            f"kernel {func_name!r}: local {local!r} is a host escape ({kind}); "
            "device locals must be LocalBuffer records"
        )
    if not isinstance(local.dtype, ScalarType):
        raise KernelIRError(
            f"kernel {func_name!r} local {local.name!r}: bad dtype {local.dtype!r}"
        )
    if not isinstance(local.scope, MemoryScope):
        raise KernelIRError(
            f"kernel {func_name!r} local {local.name!r}: bad scope {local.scope!r}"
        )
    if not isinstance(local.layout, Layout):
        raise KernelIRError(
            f"kernel {func_name!r} local {local.name!r}: bad layout {local.layout!r}"
        )
    if local.scope == MemoryScope.GLOBAL:
        raise KernelIRError(
            f"kernel {func_name!r} local {local.name!r}: local buffers cannot "
            "use global scope; global buffers are host-visible params"
        )
    if not local.shape:
        raise KernelIRError(
            f"kernel {func_name!r} local {local.name!r}: shape must be non-empty"
        )
    if any((not isinstance(dim, int)) or dim <= 0 for dim in local.shape):
        raise KernelIRError(
            f"kernel {func_name!r} local {local.name!r}: bad shape {local.shape!r}"
        )


def _classify_escape(value: Any) -> str:
    """Best-effort classification of a rejected value into an escape kind."""
    if isinstance(value, (list,)):
        return "list"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, set):
        return "set"
    if isinstance(value, tuple):
        return "tuple"
    # weakref, finalizer, generator, coroutine, or an arbitrary PyObject.
    tyname = type(value).__name__.lower()
    for kind in _REJECTED_PARAM_KINDS:
        if kind in tyname:
            return kind
    # A caller may pass an explicit sentinel tag e.g. {"kind": "weakref"}.
    if hasattr(value, "kind") and isinstance(getattr(value, "kind"), str):
        tag = getattr(value, "kind").lower()
        if tag in _REJECTED_PARAM_KINDS:
            return tag
    # Objects that define __del__ are finalizer-bearing host objects.
    if getattr(type(value), "__del__", None) is not None:
        return "finalizer"
    return "pyobject"


_INDEXED_BINARY_OPS = frozenset({"add", "sub", "mul", "div"})
_INDEXED_COMPARE_OPS = frozenset({"lt", "le", "gt", "ge", "eq", "ne"})


def _validate_indexed_expr(expr: Any, *, func_name: str) -> set[str]:
    """Validate one JSON-shaped indexed expression and return name refs."""
    if not isinstance(expr, dict):
        raise KernelIRError(
            f"kernel {func_name!r}: indexed expression must be a record, got "
            f"{type(expr).__name__}"
        )
    kind = expr.get("kind")
    if kind == "name":
        if set(expr) != {"kind", "name"}:
            raise KernelIRError(
                f"kernel {func_name!r}: indexed name has unexpected fields"
            )
        name = expr.get("name")
        if not isinstance(name, str) or not name:
            raise KernelIRError(f"kernel {func_name!r}: bad indexed name {name!r}")
        return {name}
    if kind == "literal":
        if set(expr) != {"kind", "value"}:
            raise KernelIRError(
                f"kernel {func_name!r}: indexed literal has unexpected fields"
            )
        value = expr.get("value")
        if isinstance(value, bool):
            return set()
        if isinstance(value, int) and -(1 << 31) <= value < (1 << 32):
            return set()
        if isinstance(value, float) and math.isfinite(value):
            return set()
        raise KernelIRError(
            f"kernel {func_name!r}: indexed literal must be bool/int/finite float"
        )
    if kind == "thread_id_x":
        if set(expr) != {"kind"}:
            raise KernelIRError(
                f"kernel {func_name!r}: thread_id_x expression has unexpected fields"
            )
        return set()
    if kind == "load":
        if set(expr) != {"kind", "buffer", "index"}:
            raise KernelIRError(
                f"kernel {func_name!r}: indexed load has unexpected fields"
            )
        buffer = expr.get("buffer")
        if not isinstance(buffer, str) or not buffer:
            raise KernelIRError(f"kernel {func_name!r}: indexed load has bad buffer")
        return {buffer} | _validate_indexed_expr(
            expr.get("index"), func_name=func_name
        )
    if kind == "binary":
        if set(expr) != {"kind", "op", "left", "right"}:
            raise KernelIRError(
                f"kernel {func_name!r}: indexed binary expression has unexpected fields"
            )
        op = expr.get("op")
        if op not in _INDEXED_BINARY_OPS:
            raise KernelIRError(
                f"kernel {func_name!r}: unsupported indexed binary op {op!r}"
            )
        return _validate_indexed_expr(
            expr.get("left"), func_name=func_name
        ) | _validate_indexed_expr(expr.get("right"), func_name=func_name)
    if kind == "compare":
        if set(expr) != {"kind", "op", "left", "right"}:
            raise KernelIRError(
                f"kernel {func_name!r}: indexed compare expression has unexpected fields"
            )
        op = expr.get("op")
        if op not in _INDEXED_COMPARE_OPS:
            raise KernelIRError(
                f"kernel {func_name!r}: unsupported indexed compare op {op!r}"
            )
        return _validate_indexed_expr(
            expr.get("left"), func_name=func_name
        ) | _validate_indexed_expr(expr.get("right"), func_name=func_name)
    raise KernelIRError(
        f"kernel {func_name!r}: unsupported indexed expression kind {kind!r}"
    )


def _validate_indexed_op(
    op: KernelOp,
    *,
    func_name: str,
    visible: set[str],
) -> tuple[set[str], str | None]:
    """Validate structured ops; return updated visible names + control action."""
    attrs = op.attrs
    refs: set[str]
    if op.op == "scalar_assign":
        if set(attrs) != {"target", "dtype", "declare", "expr"}:
            raise KernelIRError(
                f"kernel {func_name!r}: scalar assign has unexpected attrs"
            )
        target = attrs.get("target")
        dtype = attrs.get("dtype")
        declare = attrs.get("declare")
        if not isinstance(target, str) or not target:
            raise KernelIRError(f"kernel {func_name!r}: scalar assign has bad target")
        if dtype not in {item.value for item in ScalarType}:
            raise KernelIRError(
                f"kernel {func_name!r}: scalar assign has bad dtype {dtype!r}"
            )
        if not isinstance(declare, bool):
            raise KernelIRError(
                f"kernel {func_name!r}: scalar assign declare flag must be bool"
            )
        refs = _validate_indexed_expr(attrs.get("expr"), func_name=func_name)
        if declare and target in visible:
            raise KernelIRError(
                f"kernel {func_name!r}: scalar {target!r} redeclared in one scope"
            )
        if not declare and target not in visible:
            raise KernelIRError(
                f"kernel {func_name!r}: assignment to unknown scalar {target!r}"
            )
        updated = set(visible)
        updated.add(target)
    elif op.op == "indexed_store":
        if set(attrs) != {"index", "value"}:
            raise KernelIRError(
                f"kernel {func_name!r}: indexed store has unexpected attrs"
            )
        if not op.args:
            raise KernelIRError(f"kernel {func_name!r}: indexed store has no target")
        refs = {op.args[0]}
        refs |= _validate_indexed_expr(attrs.get("index"), func_name=func_name)
        refs |= _validate_indexed_expr(attrs.get("value"), func_name=func_name)
        updated = set(visible)
    elif op.op == "if_begin":
        if set(attrs) != {"condition"}:
            raise KernelIRError(
                f"kernel {func_name!r}: if_begin has unexpected attrs"
            )
        refs = _validate_indexed_expr(attrs.get("condition"), func_name=func_name)
        updated = set(visible)
    elif op.op in {"else", "if_end"}:
        if op.args or attrs:
            raise KernelIRError(
                f"kernel {func_name!r}: {op.op} marker must carry no args/attrs"
            )
        return set(visible), op.op
    else:
        return set(visible), None
    missing = sorted(refs - visible)
    if missing:
        raise KernelIRError(
            f"kernel {func_name!r}: op {op.op!r} references unknown symbol(s) {missing}"
        )
    if set(op.args) != refs:
        raise KernelIRError(
            f"kernel {func_name!r}: op {op.op!r} args must exactly list expression "
            f"references; got {sorted(set(op.args))}, expected {sorted(refs)}"
        )
    return updated, "if_begin" if op.op == "if_begin" else None


def validate_kernel(module: KernelModule) -> KernelModule:
    """Validate that *module* stays inside the kernel-only boundary.

    Raises :class:`KernelIRError` on the first violation. Returns *module*
    unchanged on success so it can be used inline: ``m = validate_kernel(m)``.
    """
    if not isinstance(module, KernelModule):
        raise KernelIRError(f"expected KernelModule, got {type(module).__name__}")
    if not module.funcs:
        raise KernelIRError(f"kernel module {module.name!r} has no kernel functions")

    for func in module.funcs:
        if not isinstance(func, KernelFunc):
            raise KernelIRError(
                f"module {module.name!r}: {func!r} is not a KernelFunc"
            )
        seen: set[str] = set()
        for param in func.params:
            _validate_param(param, func_name=func.name)
            pname = getattr(param, "name", None)
            if pname in seen:
                raise KernelIRError(
                    f"kernel {func.name!r}: duplicate parameter name {pname!r}"
                )
            seen.add(pname)

        for local in func.locals:
            _validate_local(local, func_name=func.name)
            lname = local.name
            if lname in seen:
                raise KernelIRError(
                    f"kernel {func.name!r}: duplicate symbol name {lname!r}"
                )
            seen.add(lname)

        visible = set(seen)
        control_stack: list[tuple[set[str], bool]] = []
        indexed_ops = {"scalar_assign", "indexed_store", "if_begin", "else", "if_end"}
        for op in func.body:
            if not isinstance(op, KernelOp):
                raise KernelIRError(
                    f"kernel {func.name!r}: body element {op!r} is not a KernelOp"
                )
            if op.op not in _ACCEPTED_OPS:
                raise KernelIRError(
                    f"kernel {func.name!r}: op {op.op!r} is not an accepted tile "
                    f"primitive (accepted: {sorted(_ACCEPTED_OPS)})"
                )
            if op.op in indexed_ops:
                updated, action = _validate_indexed_op(
                    op, func_name=func.name, visible=visible
                )
                if action == "if_begin":
                    control_stack.append((set(visible), False))
                    visible = updated
                elif action == "else":
                    if not control_stack or control_stack[-1][1]:
                        raise KernelIRError(
                            f"kernel {func.name!r}: else without unmatched if_begin"
                        )
                    base, _ = control_stack[-1]
                    control_stack[-1] = (base, True)
                    visible = set(base)
                elif action == "if_end":
                    if not control_stack:
                        raise KernelIRError(
                            f"kernel {func.name!r}: if_end without if_begin"
                        )
                    base, _ = control_stack.pop()
                    visible = set(base)
                else:
                    visible = updated
                continue
            # Op args must be plain string references to params/locals, never
            # live objects — an object here would be a device-IR escape.
            for arg in op.args:
                if not isinstance(arg, str):
                    raise KernelIRError(
                        f"kernel {func.name!r}: op {op.op!r} argument {arg!r} "
                        f"must be a name reference (str), not a live "
                        f"{type(arg).__name__} object"
                    )
                if arg and arg not in visible:
                    raise KernelIRError(
                        f"kernel {func.name!r}: op {op.op!r} references unknown "
                        f"symbol {arg!r}"
                    )
        if control_stack:
            raise KernelIRError(
                f"kernel {func.name!r}: unterminated if_begin marker"
            )
    return module


def dump_kernel(module: KernelModule) -> str:
    """Deterministic golden text dump of a kernel module.

    Stable, human-readable, and byte-round-trippable via ``parse_kernel_dict``.
    Used by the golden test.
    """
    import json

    validate_kernel(module)
    return json.dumps(module.to_dict(), indent=2, sort_keys=True)


__all__ = [
    "KernelIRError",
    "ScalarType",
    "MemoryScope",
    "Layout",
    "ScalarParam",
    "BufferParam",
    "LocalBuffer",
    "KernelOp",
    "KernelFunc",
    "KernelModule",
    "validate_kernel",
    "dump_kernel",
]
