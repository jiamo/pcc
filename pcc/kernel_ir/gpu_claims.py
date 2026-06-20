"""GPU claim-level helpers for Kernel IR hardware gates.

These helpers keep GPU evidence mode-labeled. They do not execute kernels; they
classify already-produced package/runtime results so tests and task-board
evidence cannot accidentally treat source emission, ABI packing, runtime-source
execution, pcc1 execution, and five-GC parity as the same claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_RUNTIME_INVOKED,
    STATUS_SOURCE_RUNTIME_INVOCATION_ABI_VALIDATED,
    STATUS_SOURCE_RUNTIME_PACKAGE_ABI_VALIDATED,
    STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
)
from pcc.kernel_ir.metal_invoke import STATUS_BRIDGE_INVOKED


STATUS_PCC1_METAL_LAUNCHER_EXECUTED = "pcc1_metal_launcher_executed"
STATUS_GPU_5GC_LIFETIME_EXECUTED = "gpu_5gc_lifetime_executed"
_REQUIRED_GC_BACKENDS = (0, 1, 2, 3, 4)


class GpuClaimError(ValueError):
    """A GPU result violates pcc claim hygiene."""


class GpuClaimLevel(IntEnum):
    """Durable GPU evidence levels from docs/design/pcc-gpu-next-work.md."""

    GPU_LEVEL_0_METADATA = 0
    GPU_LEVEL_1_SOURCE = 1
    GPU_LEVEL_2_ARTIFACT = 2
    GPU_LEVEL_3_RUNTIME_ABI = 3
    GPU_LEVEL_4_DEVICE_RESULT = 4
    GPU_LEVEL_5_PCC1_NATIVE = 5
    GPU_LEVEL_6_5GC_PARITY = 6


@dataclass(frozen=True)
class GpuClaimEvidence:
    """Mode-labeled evidence for one GPU primitive."""

    primitive: str
    level: GpuClaimLevel
    status: str
    proven: bool
    reason: str = ""
    runtime_launch_executed: bool = False
    runtime_source_compiled: bool = False
    fence_completed: bool = False
    cpu_oracle_matched: bool = False
    metallib_produced: bool = False
    pcc1_native_executed: bool = False
    gc_backend_parity: tuple[int, ...] = field(default_factory=tuple)
    whole_program_gpu: bool = False

    @property
    def device_result_proven(self) -> bool:
        return self.proven and self.level >= GpuClaimLevel.GPU_LEVEL_4_DEVICE_RESULT

    def to_dict(self) -> dict[str, Any]:
        return {
            "primitive": self.primitive,
            "level": self.level.name,
            "level_value": int(self.level),
            "status": self.status,
            "proven": self.proven,
            "reason": self.reason,
            "runtime_launch_executed": self.runtime_launch_executed,
            "runtime_source_compiled": self.runtime_source_compiled,
            "fence_completed": self.fence_completed,
            "cpu_oracle_matched": self.cpu_oracle_matched,
            "metallib_produced": self.metallib_produced,
            "pcc1_native_executed": self.pcc1_native_executed,
            "gc_backend_parity": list(self.gc_backend_parity),
            "whole_program_gpu": self.whole_program_gpu,
        }


def _nested(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def classify_metal_source_runtime_package_result(
    primitive: str,
    result: Any,
) -> GpuClaimEvidence:
    """Classify a runtime-source Metal package result by GPU claim level.

    A runtime-source package reaches ``GPU_LEVEL_4_DEVICE_RESULT`` only when a
    non-injected bridge call compiled source at runtime, submitted a command
    buffer, completed the fence, read device output back, and matched a CPU
    oracle. It never claims pcc1 or five-GC parity.
    """

    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    if bool(data.get("whole_program_gpu")):
        raise GpuClaimError("GPU evidence must not claim whole-program GPU execution")

    finalize = _nested(data, "finalize")
    invocation = _nested(data, "invocation")
    comparison = _nested(data, "cpu_comparison")
    source_bridge = _nested(data, "source_bridge")
    status = str(data.get("status") or "")
    reason = str(data.get("reason") or "")
    metallib_produced = bool(finalize.get("metallib_produced"))

    if status == STATUS_SKIPPED_WITH_REASON:
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
            status=status,
            proven=False,
            reason=reason or "GPU hardware/runtime was unavailable.",
            metallib_produced=metallib_produced,
        )

    runtime_launch_executed = bool(data.get("runtime_launch_executed"))
    runtime_source_compiled = bool(data.get("runtime_source_compiled"))
    fence_completed = bool(invocation.get("fence_completed"))
    cpu_oracle_matched = comparison.get("status") == "metal_cpu_oracle_match"
    bridge_called = bool(invocation.get("bridge_function_called"))
    bridge_status = str(invocation.get("status") or "")

    if (
        status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
        and bridge_status == STATUS_SOURCE_RUNTIME_INVOKED
        and runtime_launch_executed
        and runtime_source_compiled
        and fence_completed
        and cpu_oracle_matched
    ):
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_4_DEVICE_RESULT,
            status=status,
            proven=True,
            reason=reason,
            runtime_launch_executed=True,
            runtime_source_compiled=True,
            fence_completed=True,
            cpu_oracle_matched=True,
            metallib_produced=metallib_produced,
        )

    if status == STATUS_SOURCE_RUNTIME_PACKAGE_ABI_VALIDATED or (
        bridge_called and bridge_status == STATUS_SOURCE_RUNTIME_INVOCATION_ABI_VALIDATED
    ):
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_3_RUNTIME_ABI,
            status=status,
            proven=True,
            reason=reason,
            runtime_launch_executed=False,
            runtime_source_compiled=False,
            fence_completed=fence_completed,
            metallib_produced=metallib_produced,
        )

    if source_bridge.get("library_path") or source_bridge.get("object_path"):
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_2_ARTIFACT,
            status=status,
            proven=True,
            reason=reason,
            metallib_produced=metallib_produced,
        )

    if finalize.get("metal_source_produced"):
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_1_SOURCE,
            status=status,
            proven=True,
            reason=reason,
            metallib_produced=metallib_produced,
        )

    return GpuClaimEvidence(
        primitive=primitive,
        level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
        status=status,
        proven=False,
        reason=reason or "result did not prove source, artifact, ABI, or device output",
        metallib_produced=metallib_produced,
    )


def classify_pcc1_native_gpu_result(
    primitive: str,
    result: Any,
) -> GpuClaimEvidence:
    """Classify a pcc1-native Metal launcher proof by GPU claim level.

    ``GPU_LEVEL_5_PCC1_NATIVE`` requires a pcc1-built no-libpython process to
    run the same launcher path that already proves a Level-4 device result.
    A plain Level-4 runtime-source package result is intentionally not enough.
    """

    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    if bool(data.get("whole_program_gpu")):
        raise GpuClaimError("GPU evidence must not claim whole-program GPU execution")

    status = str(data.get("status") or "")
    reason = str(data.get("reason") or "")
    if status == STATUS_SKIPPED_WITH_REASON:
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
            status=status,
            proven=False,
            reason=reason or "pcc1-native Metal launcher proof was unavailable.",
        )

    finalize = _nested(data, "finalize")
    invocation = _nested(data, "invocation")
    comparison = _nested(data, "cpu_comparison")
    metallib_produced = bool(finalize.get("metallib_produced"))
    runtime_launch_executed = bool(data.get("runtime_launch_executed"))
    runtime_source_compiled = bool(data.get("runtime_source_compiled"))
    fence_completed = bool(invocation.get("fence_completed"))
    cpu_oracle_matched = comparison.get("status") == "metal_cpu_oracle_match"
    bridge_status = str(invocation.get("status") or "")
    runtime_source_device_result = (
        runtime_launch_executed
        and runtime_source_compiled
        and fence_completed
        and cpu_oracle_matched
        and bridge_status == STATUS_SOURCE_RUNTIME_INVOKED
    )
    metallib_device_result = (
        runtime_launch_executed
        and metallib_produced
        and not runtime_source_compiled
        and fence_completed
        and cpu_oracle_matched
        and bridge_status == STATUS_BRIDGE_INVOKED
    )
    device_result = runtime_source_device_result or metallib_device_result

    pcc1_native_executed = bool(data.get("pcc1_native_executed"))
    pcc1_no_libpython = bool(data.get("pcc1_no_libpython"))
    same_launcher_path = bool(data.get("same_launcher_path"))
    pcc1_returncode = data.get("pcc1_returncode")
    if (
        status == STATUS_PCC1_METAL_LAUNCHER_EXECUTED
        and device_result
        and pcc1_native_executed
        and pcc1_no_libpython
        and same_launcher_path
        and pcc1_returncode == 0
    ):
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_5_PCC1_NATIVE,
            status=status,
            proven=True,
            reason=reason,
            runtime_launch_executed=True,
            runtime_source_compiled=runtime_source_compiled,
            fence_completed=True,
            cpu_oracle_matched=True,
            metallib_produced=metallib_produced,
            pcc1_native_executed=True,
        )

    if device_result:
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_4_DEVICE_RESULT,
            status=status,
            proven=True,
            reason=reason or "device result did not include pcc1-native launcher proof",
            runtime_launch_executed=True,
            runtime_source_compiled=runtime_source_compiled,
            fence_completed=True,
            cpu_oracle_matched=True,
            metallib_produced=metallib_produced,
            pcc1_native_executed=pcc1_native_executed,
        )

    return GpuClaimEvidence(
        primitive=primitive,
        level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
        status=status,
        proven=False,
        reason=reason or "result did not prove pcc1-native GPU launcher execution",
        metallib_produced=metallib_produced,
        pcc1_native_executed=pcc1_native_executed,
    )


def classify_five_gc_gpu_lifetime_result(
    primitive: str,
    result: Any,
) -> GpuClaimEvidence:
    """Classify a real Metal lifetime proof across all five GC backends.

    Level 6 is deliberately stricter than running a host harness five times
    with different ``PCC_GC_BACKEND`` labels. Every backend record must carry
    the same pcc1-native launcher proof plus a fence-deferred native-release
    fact for the same workload.
    """

    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    if bool(data.get("whole_program_gpu")):
        raise GpuClaimError("GPU evidence must not claim whole-program GPU execution")

    status = str(data.get("status") or "")
    reason = str(data.get("reason") or "")
    if status == STATUS_SKIPPED_WITH_REASON:
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
            status=status,
            proven=False,
            reason=reason or "five-GC Metal lifetime proof was unavailable.",
        )

    backend_results = data.get("backend_results")
    if not isinstance(backend_results, list) or not backend_results:
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
            status=status,
            proven=False,
            reason=reason or "five-GC proof requires per-backend result records",
        )

    workload_id = data.get("workload_id")
    if not isinstance(workload_id, str) or not workload_id:
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
            status=status,
            proven=False,
            reason=reason or "five-GC proof requires a stable workload_id",
        )

    seen: set[int] = set()
    min_level = GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY
    metallib_produced = False
    runtime_source_compiled = False
    for item in backend_results:
        if not isinstance(item, dict):
            return GpuClaimEvidence(
                primitive=primitive,
                level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
                status=status,
                proven=False,
                reason="five-GC proof backend records must be dictionaries",
            )
        backend = item.get("gc_backend")
        if backend not in _REQUIRED_GC_BACKENDS or backend in seen:
            return GpuClaimEvidence(
                primitive=primitive,
                level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
                status=status,
                proven=False,
                reason=f"five-GC proof has invalid or duplicate backend {backend!r}",
            )
        seen.add(backend)
        if item.get("pcc_gc_backend_env") != backend:
            return GpuClaimEvidence(
                primitive=primitive,
                level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
                status=status,
                proven=False,
                reason=f"backend {backend}: PCC_GC_BACKEND env marker does not match",
            )
        if bool(item.get("env_label_only")):
            return GpuClaimEvidence(
                primitive=primitive,
                level=GpuClaimLevel.GPU_LEVEL_4_DEVICE_RESULT,
                status=status,
                proven=True,
                reason=(
                    f"backend {backend}: host/env-label-only run is not "
                    "GPU_LEVEL_6_5GC_PARITY"
                ),
                runtime_launch_executed=True,
                runtime_source_compiled=True,
                fence_completed=True,
                cpu_oracle_matched=True,
                gc_backend_parity=tuple(sorted(seen)),
            )
        if item.get("workload_id") != workload_id:
            return GpuClaimEvidence(
                primitive=primitive,
                level=GpuClaimLevel.GPU_LEVEL_0_METADATA,
                status=status,
                proven=False,
                reason=f"backend {backend}: workload_id does not match",
            )
        item_status = str(item.get("status") or STATUS_PCC1_METAL_LAUNCHER_EXECUTED)
        pcc1_evidence = classify_pcc1_native_gpu_result(
            primitive,
            {**item, "status": item_status},
        )
        min_level = min(min_level, pcc1_evidence.level)
        metallib_produced = metallib_produced or pcc1_evidence.metallib_produced
        runtime_source_compiled = (
            runtime_source_compiled or pcc1_evidence.runtime_source_compiled
        )
        if not (
            pcc1_evidence.proven
            and pcc1_evidence.level >= GpuClaimLevel.GPU_LEVEL_5_PCC1_NATIVE
        ):
            return GpuClaimEvidence(
                primitive=primitive,
                level=pcc1_evidence.level,
                status=status,
                proven=pcc1_evidence.proven,
                reason=(
                    f"backend {backend}: missing pcc1-native Level-5 proof "
                    f"({pcc1_evidence.reason})"
                ),
                runtime_launch_executed=pcc1_evidence.runtime_launch_executed,
                runtime_source_compiled=pcc1_evidence.runtime_source_compiled,
                fence_completed=pcc1_evidence.fence_completed,
                cpu_oracle_matched=pcc1_evidence.cpu_oracle_matched,
                metallib_produced=metallib_produced,
                pcc1_native_executed=pcc1_evidence.pcc1_native_executed,
                gc_backend_parity=tuple(sorted(seen)),
            )
        if item.get("native_release_before_fence") not in (None, False):
            return GpuClaimEvidence(
                primitive=primitive,
                level=GpuClaimLevel.GPU_LEVEL_5_PCC1_NATIVE,
                status=status,
                proven=True,
                reason=f"backend {backend}: native release happened before fence completion",
                runtime_launch_executed=True,
                runtime_source_compiled=runtime_source_compiled,
                fence_completed=True,
                cpu_oracle_matched=True,
                metallib_produced=metallib_produced,
                pcc1_native_executed=True,
                gc_backend_parity=tuple(sorted(seen)),
            )
        if item.get("native_release_after_fence") is not True:
            return GpuClaimEvidence(
                primitive=primitive,
                level=GpuClaimLevel.GPU_LEVEL_5_PCC1_NATIVE,
                status=status,
                proven=True,
                reason=f"backend {backend}: missing fence-deferred native release proof",
                runtime_launch_executed=True,
                runtime_source_compiled=runtime_source_compiled,
                fence_completed=True,
                cpu_oracle_matched=True,
                metallib_produced=metallib_produced,
                pcc1_native_executed=True,
                gc_backend_parity=tuple(sorted(seen)),
            )

    if tuple(sorted(seen)) != _REQUIRED_GC_BACKENDS:
        return GpuClaimEvidence(
            primitive=primitive,
            level=min_level,
            status=status,
            proven=False,
            reason=f"five-GC proof requires backends {list(_REQUIRED_GC_BACKENDS)}, got {sorted(seen)}",
            metallib_produced=metallib_produced,
            gc_backend_parity=tuple(sorted(seen)),
        )

    if status != STATUS_GPU_5GC_LIFETIME_EXECUTED:
        return GpuClaimEvidence(
            primitive=primitive,
            level=GpuClaimLevel.GPU_LEVEL_5_PCC1_NATIVE,
            status=status,
            proven=True,
            reason="all backend records passed, but top-level status is not the Level-6 status",
            runtime_launch_executed=True,
            runtime_source_compiled=runtime_source_compiled,
            fence_completed=True,
            cpu_oracle_matched=True,
            metallib_produced=metallib_produced,
            pcc1_native_executed=True,
            gc_backend_parity=_REQUIRED_GC_BACKENDS,
        )

    return GpuClaimEvidence(
        primitive=primitive,
        level=GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY,
        status=status,
        proven=True,
        reason=reason,
        runtime_launch_executed=True,
        runtime_source_compiled=runtime_source_compiled,
        fence_completed=True,
        cpu_oracle_matched=True,
        metallib_produced=metallib_produced,
        pcc1_native_executed=True,
        gc_backend_parity=_REQUIRED_GC_BACKENDS,
    )


def require_device_result_or_skip(
    evidence: GpuClaimEvidence,
    *,
    strict: bool = False,
) -> GpuClaimEvidence:
    """Accept a skip by default, but require level 4 when strict is enabled."""

    if evidence.device_result_proven:
        return evidence
    if evidence.status == STATUS_SKIPPED_WITH_REASON and not strict:
        return evidence
    raise GpuClaimError(
        f"{evidence.primitive} did not prove GPU_LEVEL_4_DEVICE_RESULT: "
        f"{evidence.to_dict()}"
    )


def require_pcc1_native_or_skip(
    evidence: GpuClaimEvidence,
    *,
    strict: bool = False,
) -> GpuClaimEvidence:
    """Accept a mode-labeled skip, but never accept Level 4 as Level 5."""

    if evidence.proven and evidence.level >= GpuClaimLevel.GPU_LEVEL_5_PCC1_NATIVE:
        return evidence
    if evidence.status == STATUS_SKIPPED_WITH_REASON and not strict:
        return evidence
    raise GpuClaimError(
        f"{evidence.primitive} did not prove GPU_LEVEL_5_PCC1_NATIVE: "
        f"{evidence.to_dict()}"
    )


def require_five_gc_parity_or_skip(
    evidence: GpuClaimEvidence,
    *,
    strict: bool = False,
) -> GpuClaimEvidence:
    """Accept a mode-labeled skip, but never accept Level 4/5 as Level 6."""

    if evidence.proven and evidence.level >= GpuClaimLevel.GPU_LEVEL_6_5GC_PARITY:
        return evidence
    if evidence.status == STATUS_SKIPPED_WITH_REASON and not strict:
        return evidence
    raise GpuClaimError(
        f"{evidence.primitive} did not prove GPU_LEVEL_6_5GC_PARITY: "
        f"{evidence.to_dict()}"
    )


__all__ = [
    "GpuClaimError",
    "GpuClaimEvidence",
    "GpuClaimLevel",
    "STATUS_GPU_5GC_LIFETIME_EXECUTED",
    "STATUS_PCC1_METAL_LAUNCHER_EXECUTED",
    "classify_five_gc_gpu_lifetime_result",
    "classify_metal_source_runtime_package_result",
    "classify_pcc1_native_gpu_result",
    "require_device_result_or_skip",
    "require_five_gc_parity_or_skip",
    "require_pcc1_native_or_skip",
]
