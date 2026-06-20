"""PCC TIRx-compatible lowering adapter.

Row K-P0-TIRX-ADAPTER. Freezes pcc Kernel IR tile semantics into a plain-TIR
shape, mirroring TIRx's ``LowerTIRx`` (== ``TilePrimitiveDispatch`` +
``LowerTIRxCleanup``): tile primitives are dispatched to concrete ops and the
result is tagged with a ``plain_tir_freeze`` marker. After that marker, tile
primitives / TileLayout / execution-scope ids are gone — the module is "plain
TIR" (see docs/design/pcc-kernel-ir.md §2).

First-slice scope: a golden freeze for ``copy`` / ``atomic_add`` / ``fill`` /
``parallel`` / ``swizzle`` metadata (and ``gemm`` / ``gemm_sp`` /
``elementwise_add``), plus a negative rule: **CUDA-only
assumptions are REJECTED for a Metal target** (research report §优化通道: Metal
must not be polluted by CUDA-specific passes like LowerHopperIntrin / cp.async /
tcgen05). No real device codegen.

Importable standalone::

    from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir, TirxAdapterError
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pcc.kernel_ir.ir import KernelModule, validate_kernel

PLAIN_TIR_FREEZE_MARKER = "plain_tir_freeze"

# Tile primitives this adapter knows how to freeze into plain TIR.
_DISPATCH: dict[str, str] = {
    "copy": "tir.copy_loop",
    "atomic_add": "tir.atomic_add",
    "copy_async": "tir.copy_async_loop",
    "fill": "tir.fill_loop",
    "gemm": "tir.gemm_expand",
    "gemm_sp": "tir.gemm_sp_expand",
    "reduce": "tir.reduce_loop",
    "elementwise_add": "tir.elementwise_add",
    "scalar_assign": "tir.scalar_assign",
    "indexed_store": "tir.indexed_store",
    "if_begin": "tir.if_begin",
    "else": "tir.else",
    "if_end": "tir.if_end",
    "parallel": "tir.parallel_for",
    "barrier": "tir.barrier",
    "fence": "tir.fence",
    "swizzle": "tir.use_swizzle",
    "layout_annotation": "tir.annotate_layout",
}

# Op attributes that encode a CUDA-only assumption. If any of these appears
# while targeting Metal, we fail fast rather than silently degrade — this is the
# "CUDA-only assumptions rejected for Metal" negative rule.
_CUDA_ONLY_ATTRS = frozenset(
    {
        "cp_async",
        "tcgen05",
        "hopper_tma",
        "wgmma",
        "blackwell_2sm",
        "ptx",
        "nvvm_intrin",
        "l2_persistent",
    }
)


class TirxAdapterError(ValueError):
    """A construct could not be frozen to plain TIR for the requested target."""


@dataclass(frozen=True)
class PlainTirOp:
    """A frozen (plain-TIR) op. Tile-primitive identity is gone; only a
    concrete lowered op name + resolved args remain."""

    tir_op: str
    args: tuple[str, ...]
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tir_op": self.tir_op, "args": list(self.args), "attrs": dict(self.attrs)}


@dataclass(frozen=True)
class PlainTirModule:
    """A frozen module. Carries the ``plain_tir_freeze`` marker so downstream
    passes know tile semantics are already resolved."""

    module: str
    target: str
    marker: str
    funcs: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "target": self.target,
            "marker": self.marker,
            "plain_tir": True,
            "funcs": self.funcs,
        }


def _dispatch_op(op_name: str, target: str) -> str:
    tir_op = _DISPATCH.get(op_name)
    if tir_op is None:
        raise TirxAdapterError(
            f"tile primitive {op_name!r} has no plain-TIR dispatch "
            f"(known: {sorted(_DISPATCH)})"
        )
    return tir_op


def _check_cuda_only(op_name: str, attrs: dict[str, Any], target: str) -> None:
    if target == "metal":
        offending = sorted(_CUDA_ONLY_ATTRS & set(attrs.keys()))
        if offending:
            raise TirxAdapterError(
                f"op {op_name!r} carries CUDA-only assumption(s) {offending} but "
                f"the target is 'metal'; CUDA-specific lowering (cp.async / "
                f"tcgen05 / Hopper TMA / wgmma / Blackwell) must not pollute the "
                f"Metal path. Fail fast rather than silently degrade."
            )
        # async_copy on Metal has no native lowering in this slice — the Metal
        # pipeline strips software pipelining and has no cp.async. Reject rather
        # than pretend.
        if op_name == "copy_async":
            raise TirxAdapterError(
                "T.async_copy / copy_async is not lowerable on the Metal target "
                "in this slice (no async-copy lowering); use a synchronous copy. "
                "Silent fallback to a sync copy would change semantics."
            )


def lower_to_plain_tir(module: KernelModule, target: str = "metal") -> PlainTirModule:
    """Freeze a validated kernel module into a plain-TIR module for *target*.

    Raises :class:`TirxAdapterError` if any op carries a CUDA-only assumption
    while targeting Metal, or if an op has no plain-TIR dispatch.
    """
    validate_kernel(module)
    target = str(target).lower()

    frozen_funcs: list[dict[str, Any]] = []
    for func in module.funcs:
        frozen_ops: list[dict[str, Any]] = []
        for op in func.body:
            _check_cuda_only(op.op, op.attrs, target)
            tir_op = _dispatch_op(op.op, target)
            frozen_ops.append(
                PlainTirOp(tir_op=tir_op, args=op.args, attrs=dict(op.attrs)).to_dict()
            )
        frozen_funcs.append(
            {
                "name": func.name,
                "grid": list(func.grid),
                "threads": func.threads,
                "params": [p.to_dict() for p in func.params],
                "locals": [l.to_dict() for l in func.locals],
                "ops": frozen_ops,
            }
        )

    return PlainTirModule(
        module=module.name,
        target=target,
        marker=PLAIN_TIR_FREEZE_MARKER,
        funcs=frozen_funcs,
    )


def freeze_dump(module: KernelModule, target: str = "metal") -> str:
    """Deterministic golden dump of the frozen plain-TIR module."""
    import json

    return json.dumps(lower_to_plain_tir(module, target).to_dict(), indent=2, sort_keys=True)


__all__ = [
    "PLAIN_TIR_FREEZE_MARKER",
    "TirxAdapterError",
    "PlainTirOp",
    "PlainTirModule",
    "lower_to_plain_tir",
    "freeze_dump",
]
