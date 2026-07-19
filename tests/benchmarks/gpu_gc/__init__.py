"""tests/benchmarks/gpu_gc — CPU-only surrogate measurement harness for the
``pcc.gpu_gc`` GPU-participating-GC oracle package.

FIRST SLICE (P-P0-GPU-GC-BENCH). This is a **measurement** harness, not a
collector and not a benchmark that ranks collectors. It exercises the CPU-only
oracle (``pcc.gpu_gc.substrate`` / ``cpu_collector`` / ``assist`` /
``metal_adapter`` / ``tiered``) and reports **deterministic logical counters**:
mark/sweep step counts, page classification ratios (GPU_TRACEABLE vs CPU_ONLY
vs GPU_SUMMARY_ONLY), assist fallback telemetry, and tiered block hit/recompute
counts.

Device-mode taxonomy (see ``DeviceMode``):

* ``cpu-only``        -> RUNS. Everything measured here is a CPU logical
  surrogate: no wall-clock, no capacity, no throughput claim.
* ``cuda-assisted``   -> SKIPPED_WITH_REASON (no CUDA device / kernel here).
* ``metal-assisted``  -> SKIPPED_WITH_REASON (no Metal tooling; the
  ``MetalResidencyAdapter`` itself reports absence).

CLAIM BOUNDARY: measurement target only. These counters describe the *oracle
model's* logical behavior. They are **not** a completed GPU/Metal collector,
**not** a throughput/latency/capacity result, and **not** a collector ranking.
Every "pause" / "RSS" / "fragmentation" number below is a *surrogate logical
counter* (step counts / page counts / span counts), never a measured resource.

Design and full claim boundary: ``docs/design/pcc-gpu-gc-bench.md``.
"""
from __future__ import annotations

from .harness import (
    DeviceMode,
    ModeAvailability,
    device_mode_availability,
    SkipTaxonomy,
    SubstrateProfile,
    CollectorStepCounts,
    ClassificationRatios,
    AssistOracleReport,
    TieredReport,
    ServingSurrogateCounters,
    CpuOnlyBenchResult,
    run_cpu_only_bench,
    run_serving_surrogate,
    build_classified_substrate,
)

__all__ = [
    "DeviceMode",
    "ModeAvailability",
    "device_mode_availability",
    "SkipTaxonomy",
    "SubstrateProfile",
    "CollectorStepCounts",
    "ClassificationRatios",
    "AssistOracleReport",
    "TieredReport",
    "ServingSurrogateCounters",
    "CpuOnlyBenchResult",
    "run_cpu_only_bench",
    "run_serving_surrogate",
    "build_classified_substrate",
]
