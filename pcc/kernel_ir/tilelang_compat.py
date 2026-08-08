"""PCC TileLang compatibility — parse/inspect-only construct matrix.

Row K-P0-TILELANG-COMPAT. This is NOT a TileLang runtime and NOT a code
generator. It is an *inspect-only* classifier: given a TileLang-style construct
name (as it appears in ``tilelang.language``), say whether pcc's first-slice
kernel subset ACCEPTS it (maps to a pcc Kernel IR construct) or REJECTS it
(out of scope for the first slice).

Accepted subset (research report §适合先原生化的子集):
    kernel / tensor / buffer / shared / fragment / local
    parallel / serial / vectorized / pipelined
    copy / copy_async / fill / gemm / bounded static reduce_sum
    layout annotation / launch binding / barrier / fence

Explicitly out of scope: CuTeDSL, Hopper/Blackwell/TMA intrinsics, full runtime.

Importable standalone::

    from pcc.kernel_ir.tilelang_compat import classify, CONSTRUCT_MATRIX
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Support(enum.Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ConstructInfo:
    """One row of the accepted/rejected matrix."""

    tilelang_name: str
    support: Support
    pcc_construct: str | None  # the pcc Kernel IR mapping when accepted
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "tilelang_name": self.tilelang_name,
            "support": self.support.value,
            "pcc_construct": self.pcc_construct,
            "reason": self.reason,
        }


def _ok(name: str, pcc: str, reason: str) -> ConstructInfo:
    return ConstructInfo(name, Support.ACCEPTED, pcc, reason)


def _no(name: str, reason: str) -> ConstructInfo:
    return ConstructInfo(name, Support.REJECTED, None, reason)


# The accepted/rejected construct matrix. Names match tilelang.language symbols.
_MATRIX: dict[str, ConstructInfo] = {info.tilelang_name: info for info in (
    # --- accepted: the high-value first-slice subset ---
    _ok("prim_func", "KernelFunc", "kernel function definition"),
    _ok("Kernel", "KernelFunc.grid/threads", "grid/thread launch binding"),
    _ok("Tensor", "BufferParam", "typed buffer parameter"),
    _ok("Buffer", "BufferParam", "typed buffer parameter"),
    _ok("alloc_shared", "MemoryScope.SHARED", "block-visible scratchpad"),
    _ok("alloc_fragment", "MemoryScope.FRAGMENT", "per-thread register tile"),
    _ok("alloc_local", "MemoryScope.LOCAL", "thread-private storage"),
    _ok("alloc_global", "MemoryScope.GLOBAL", "workspace (capability-gated)"),
    _ok("Parallel", "KernelOp('parallel')", "structured parallel loop"),
    _ok("vectorized", "KernelOp attr vectorized_extent", "one-dimensional tile-copy metadata"),
    _ok("Pipelined", "KernelOp attr num_stages", "software pipeline annotation"),
    _ok(
        "use_swizzle",
        "KernelOp('swizzle')",
        "tile rasterization metadata; row/column scalar-GEMM and simdgroup-GEMM source paths",
    ),
    _ok(
        "annotate_layout",
        "KernelOp('layout_annotation') / LocalBuffer.layout",
        "empty no-op or shared-buffer make_swizzled_layout metadata; rank-2 bank-swizzled and padded A/B shared tiles execute in Metal source",
    ),
    _ok("copy", "KernelOp('copy')", "surface-synchronous tile copy"),
    _ok(
        "atomic_add",
        "KernelOp('atomic_add')",
        "split-k f32 output accumulation for the scalar GEMM subset, including explicit ceildiv K tails",
    ),
    _ok("async_copy", "KernelOp('copy_async')", "explicit-async copy (needs fence)"),
    _ok("gemm", "KernelOp('gemm')", "tile gemm via primitive dispatch"),
    _ok("clear", "KernelOp('fill', value=0)", "zero/fill primitive"),
    _ok("fill", "KernelOp('fill')", "fill primitive"),
    _ok(
        "reduce_sum",
        "KernelOp('reduce', reduction='sum')",
        "bounded static contiguous last-dimension row reduction; pcc source subset only",
    ),
    # --- rejected: out of scope for the first slice ---
    _no("reduce_max", "max reduction is not yet lowered by the pcc Metal source path"),
    _no("reduce_min", "min reduction is not yet lowered by the pcc Metal source path"),
    _no("reduce_abssum", "absolute-sum reduction is not yet lowered by the pcc Metal source path"),
    _no("reduce_absmax", "absolute-max reduction is not yet lowered by the pcc Metal source path"),
    _no("alloc_tmem", "Blackwell tensor-memory; out of scope"),
    _no("alloc_tcgen", "tcgen05 tensor-core-gen; CUDA-only, out of scope"),
    _no("alloc_wgmma", "Hopper wgmma descriptor; CUDA-only, out of scope"),
    _no("alloc_cluster", "thread-block cluster (Hopper); out of scope"),
    _no("alloc_descriptor", "TMA descriptor (Hopper); out of scope"),
    _no("alloc_barrier", "mbarrier async barrier; deferred (use fence)"),
    _no("cute", "CuTeDSL backend; out of scope"),
    _no("cutedsl", "CuTeDSL backend; out of scope"),
    _no("tma_load", "Hopper TMA intrinsic; out of scope"),
    _no("tma_store", "Hopper TMA intrinsic; out of scope"),
    _no("pragma_import_c", "raw C import; escapes the kernel-only boundary"),
)}

CONSTRUCT_MATRIX: dict[str, dict[str, object]] = {
    name: info.to_dict() for name, info in _MATRIX.items()
}


class TileLangCompatError(KeyError):
    """A TileLang construct is unknown to the compatibility layer."""


def classify(construct: str) -> ConstructInfo:
    """Classify a TileLang construct name. Accepts an optional ``T.`` prefix.

    Raises :class:`TileLangCompatError` for a name the matrix does not know —
    unknown constructs are NOT silently accepted (that would be a false
    compatibility claim).
    """
    name = construct[2:] if construct.startswith("T.") else construct
    info = _MATRIX.get(name)
    if info is None:
        raise TileLangCompatError(
            f"TileLang construct {construct!r} is not in the pcc compatibility "
            f"matrix; it is neither accepted nor explicitly rejected. Do not "
            f"claim support for it."
        )
    return info


def is_accepted(construct: str) -> bool:
    return classify(construct).support is Support.ACCEPTED


def accepted_names() -> list[str]:
    return sorted(n for n, i in _MATRIX.items() if i.support is Support.ACCEPTED)


def rejected_names() -> list[str]:
    return sorted(n for n, i in _MATRIX.items() if i.support is Support.REJECTED)


__all__ = [
    "Support",
    "ConstructInfo",
    "CONSTRUCT_MATRIX",
    "TileLangCompatError",
    "classify",
    "is_accepted",
    "accepted_names",
    "rejected_names",
]
