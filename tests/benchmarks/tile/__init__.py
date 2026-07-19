"""tests/benchmarks/tile — metadata-only Tile/TIRx/Metal measurement harness.

FIRST SLICE (P-P0-TILE-TVM-BENCH). This is a **measurement** harness over the
existing ``pcc.kernel_ir`` package, not a device benchmark and not a runtime. It
builds kernel IR for a fixed set of kernel shapes (``vector-add`` / ``copy`` /
``fill`` / ``reduction`` / ``gemm``), freezes them through the TIRx-compatible
adapter, splits host/device via the TargetMachine registry, and reports
**logical compile-side metrics only**.

Mode taxonomy (see ``TileBenchMode``):

* ``cpu-only``          -> RUNS. IR node counts, host/device split node counts,
  plain-TIR freeze success, TVM-oracle golden match. No wall-clock, no TFLOPS.
* ``metal-source-only`` -> RUNS iff ``metal_finalize`` reports a descriptor,
  else SKIPPED_WITH_REASON. Measures device-source descriptor metadata +
  packaging plan. No ``.metallib``, no device codegen.
* ``metal-runtime``     -> ALWAYS SKIPPED_WITH_REASON. No host launch claimed.

CLAIM BOUNDARY: measurement target only. Every launch-latency / TFLOPS /
throughput value is the literal placeholder ``not-measured``. A speed claim
requires BOTH IR-shape evidence AND a real hardware run — neither exists here.

Design and full claim boundary: ``docs/design/pcc-tile-bench.md``.
"""
from __future__ import annotations

from .harness import (
    NOT_MEASURED,
    TileBenchMode,
    RunStatus,
    KERNEL_SHAPES,
    build_kernel,
    IrNodeCounts,
    HostDeviceSplit,
    ResourcePlaceholders,
    CpuOnlyKernelReport,
    MetalSourceOnlyReport,
    MetalRuntimeReport,
    metal_toolchain_available,
    run_cpu_only_bench,
    run_metal_source_only,
    run_metal_runtime,
    run_all_modes,
)

__all__ = [
    "NOT_MEASURED",
    "TileBenchMode",
    "RunStatus",
    "KERNEL_SHAPES",
    "build_kernel",
    "IrNodeCounts",
    "HostDeviceSplit",
    "ResourcePlaceholders",
    "CpuOnlyKernelReport",
    "MetalSourceOnlyReport",
    "MetalRuntimeReport",
    "metal_toolchain_available",
    "run_cpu_only_bench",
    "run_metal_source_only",
    "run_metal_runtime",
    "run_all_modes",
]
