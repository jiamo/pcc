"""Metallib-backed Metal package execution for Kernel IR launches.

This is the offline-artifact counterpart to ``metal_source_runtime.py``. It
requires a produced ``.metallib`` and loads it through the generated
``newLibraryWithURL`` bridge before it can claim a command-buffer launch.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pcc.kernel_ir.hmm_fence import PccFenceToken
from pcc.kernel_ir.metal_buffer import (
    MetalNativeBufferAllocationSet,
    MetalNativeBufferRuntimeArtifacts,
)
from pcc.kernel_ir.metal_invoke import MetalBridgeInvocationResult
from pcc.kernel_ir.metal_package import MetalKernelPackage
from pcc.kernel_ir.metal_tensor import MetalMatrixTransferSet
from pcc.kernel_ir.metal_verify import MetalCpuOracleComparisonResult
from pcc.kernel_ir.tirx_adapter import PlainTirModule

STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED = "metal_metallib_runtime_package_executed"
STATUS_METALLIB_RUNTIME_PACKAGE_ABI_VALIDATED = (
    "metal_metallib_runtime_package_abi_validated"
)
STATUS_METALLIB_RUNTIME_PACKAGE_FAILED = "metal_metallib_runtime_package_failed"
STATUS_SKIPPED_WITH_REASON = "SKIPPED_WITH_REASON"


class MetalMetallibRuntimeError(ValueError):
    """A metallib-backed Metal package launch violates the runtime contract."""


@dataclass(frozen=True)
class MetalMetallibRuntimePackageResult:
    """End-to-end metallib-backed package/run result for one Kernel IR launch."""

    status: str
    package_status: str | None
    module_name: str | None
    artifact_dir: str
    package: MetalKernelPackage | None = None
    native_buffer_runtime: MetalNativeBufferRuntimeArtifacts | None = None
    invocation: MetalBridgeInvocationResult | None = None
    matrix_write: MetalMatrixTransferSet | None = None
    cpu_comparison: MetalCpuOracleComparisonResult | None = None
    allocation_snapshot: dict[str, Any] | None = None
    allocations_released: bool = False
    reason: str = ""
    metallib_produced: bool = False
    runtime_launch_executed: bool = False
    runtime_source_compiled: bool = False
    whole_program_gpu: bool = False
    claim_mode: str = "Metal metallib-backed package execution"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "claim_mode": self.claim_mode,
            "package_status": self.package_status,
            "module_name": self.module_name,
            "artifact_dir": self.artifact_dir,
            "metallib_produced": self.metallib_produced,
            "runtime_launch_executed": self.runtime_launch_executed,
            "runtime_source_compiled": self.runtime_source_compiled,
            "whole_program_gpu": self.whole_program_gpu,
            "reason": self.reason,
            "package": self.package.to_dict() if self.package else None,
            "native_buffer_runtime": (
                self.native_buffer_runtime.to_dict()
                if self.native_buffer_runtime
                else None
            ),
            "matrix_write": self.matrix_write.to_dict() if self.matrix_write else None,
            "invocation": self.invocation.to_dict() if self.invocation else None,
            "cpu_comparison": (
                self.cpu_comparison.to_dict() if self.cpu_comparison else None
            ),
            "allocation_snapshot": self.allocation_snapshot,
            "allocations_released": self.allocations_released,
        }


def _result(
    *,
    status: str,
    artifact_dir: Path,
    package: MetalKernelPackage | None = None,
    native_buffer_runtime: MetalNativeBufferRuntimeArtifacts | None = None,
    invocation: MetalBridgeInvocationResult | None = None,
    matrix_write: MetalMatrixTransferSet | None = None,
    cpu_comparison: MetalCpuOracleComparisonResult | None = None,
    allocation_set: MetalNativeBufferAllocationSet | None = None,
    reason: str,
) -> MetalMetallibRuntimePackageResult:
    allocation_snapshot = allocation_set.to_dict() if allocation_set is not None else None
    runtime_launch_executed = bool(
        invocation is not None
        and invocation.runtime_launch_executed
        and cpu_comparison is not None
        and cpu_comparison.runtime_launch_executed
    )
    return MetalMetallibRuntimePackageResult(
        status=status,
        package_status=package.status if package is not None else None,
        module_name=package.module_name if package is not None else None,
        artifact_dir=str(artifact_dir),
        package=package,
        native_buffer_runtime=native_buffer_runtime,
        invocation=invocation,
        matrix_write=matrix_write,
        cpu_comparison=cpu_comparison,
        allocation_snapshot=allocation_snapshot,
        allocations_released=allocation_set.released if allocation_set is not None else False,
        reason=reason,
        metallib_produced=bool(
            package is not None and package.finalize.metallib_produced
        ),
        runtime_launch_executed=runtime_launch_executed,
    )


def run_metal_metallib_runtime_package(
    module: Any,
    packed_args: Any,
    artifact_dir: str | Path,
    *,
    input_matrices: Mapping[str, object],
    cpu_reference: Any,
    output_name: str | None = None,
    entry: str | None = None,
    zero_fill_unprovided: bool = True,
    wait_until_completed: bool = True,
    timeout: float = 30.0,
    metal_source_emitter: Callable[[PlainTirModule], str] | None = None,
    metal_source_tool: str = "pcc.kernel_ir.metal_finalize.emit_metal_source",
    package_bridge_compiler: Callable[..., Path] | None = None,
    package_bridge_linker: Callable[..., Path] | None = None,
    package_bridge_loader: Callable[..., str] | None = None,
    native_buffer_compiler: Callable[..., Path] | None = None,
    native_buffer_linker: Callable[..., Path] | None = None,
    native_buffer_loader: Callable[..., str] | None = None,
    buffer_cdll_factory: Callable[[str], Any] | None = None,
    bridge_cdll_factory: Callable[[str], Any] | None = None,
) -> MetalMetallibRuntimePackageResult:
    """Build, run, and verify one offline ``.metallib`` Metal package.

    A successful result requires all of:

    * produced ``.metal``, ``.air``, and ``.metallib`` artifacts
    * validated host bridge dylib symbol
    * native ``id<MTLBuffer>`` bindings for every buffer argument
    * bridge invocation success
    * readback matching the supplied CPU oracle
    """
    from pcc.kernel_ir.metal_buffer import (
        allocate_metal_native_buffers_for_plan,
        build_metal_native_buffer_runtime_artifacts,
    )
    from pcc.kernel_ir.metal_invoke import (
        STATUS_BRIDGE_INVOCATION_ABI_VALIDATED,
        invoke_metal_bridge_packet,
    )
    from pcc.kernel_ir.metal_package import (
        build_metal_bridge_invocation_packet,
        build_metal_kernel_package,
    )
    from pcc.kernel_ir.metal_tensor import write_metal_launch_matrices
    from pcc.kernel_ir.metal_verify import (
        verify_metal_launch_output_against_cpu_reference,
    )

    out_dir = Path(artifact_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    probe_package = build_metal_kernel_package(
        module,
        packed_args,
        out_dir / "package",
        entry=entry,
        compile_metal=True,
        metal_source_emitter=metal_source_emitter,
        metal_source_tool=metal_source_tool,
        timeout=timeout,
    )
    if not probe_package.finalize.metallib_produced:
        reason = probe_package.finalize.reason or "no produced metallib artifact"
        return _result(
            status=STATUS_SKIPPED_WITH_REASON,
            artifact_dir=out_dir,
            package=probe_package,
            reason=reason,
        )

    package = build_metal_kernel_package(
        module,
        packed_args,
        out_dir / "package",
        entry=entry,
        compile_metal=True,
        compile_bridge=True,
        link_bridge_library=True,
        validate_bridge_library=True,
        metal_source_emitter=metal_source_emitter,
        metal_source_tool=metal_source_tool,
        bridge_compiler=package_bridge_compiler,
        bridge_linker=package_bridge_linker,
        bridge_loader=package_bridge_loader,
        timeout=timeout,
    )
    native_buffer_runtime = build_metal_native_buffer_runtime_artifacts(
        out_dir / "native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
        compiler=native_buffer_compiler,
        linker=native_buffer_linker,
        loader=native_buffer_loader,
        timeout=timeout,
    )
    if native_buffer_runtime.status == STATUS_SKIPPED_WITH_REASON:
        return _result(
            status=STATUS_SKIPPED_WITH_REASON,
            artifact_dir=out_dir,
            package=package,
            native_buffer_runtime=native_buffer_runtime,
            reason=native_buffer_runtime.reason,
        )
    if native_buffer_runtime.library_path is None:
        raise MetalMetallibRuntimeError("native buffer runtime did not produce a dylib")

    allocation_set: MetalNativeBufferAllocationSet | None = None
    matrix_write = None
    invocation = None
    comparison = None
    status = STATUS_METALLIB_RUNTIME_PACKAGE_FAILED
    reason = "Metallib-backed package execution did not complete."
    try:
        allocation_set = allocate_metal_native_buffers_for_plan(
            native_buffer_runtime.library_path,
            package.launch_plan,
            cdll_factory=buffer_cdll_factory,
        )
        if allocation_set.status == STATUS_SKIPPED_WITH_REASON:
            status = STATUS_SKIPPED_WITH_REASON
            reason = allocation_set.reason
        else:
            if allocation_set.binding_set is None:
                raise MetalMetallibRuntimeError(
                    "native buffer allocation did not produce bindings"
                )
            matrix_write = write_metal_launch_matrices(
                native_buffer_runtime.library_path,
                allocation_set,
                package.launch_plan,
                input_matrices,
                zero_fill_unprovided=zero_fill_unprovided,
                cdll_factory=buffer_cdll_factory,
            )
            packet = build_metal_bridge_invocation_packet(
                package,
                wait_until_completed=wait_until_completed,
                allow_missing_metallib=False,
                native_buffer_bindings=allocation_set.binding_set,
            )
            fence = PccFenceToken()
            invocation = invoke_metal_bridge_packet(
                packet,
                fence=fence,
                cdll_factory=bridge_cdll_factory,
            )
            if invocation.runtime_launch_executed:
                comparison = verify_metal_launch_output_against_cpu_reference(
                    native_buffer_runtime.library_path,
                    allocation_set,
                    package.launch_plan,
                    cpu_reference,
                    output_name=output_name,
                    cdll_factory=buffer_cdll_factory,
                    runtime_launch_executed=True,
                )
                status = STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED
                reason = (
                    "Metallib-backed Metal package loaded the produced metallib, "
                    "submitted a command buffer, completed the fence, and "
                    "matched the CPU oracle."
                )
            elif invocation.status == STATUS_BRIDGE_INVOCATION_ABI_VALIDATED:
                status = STATUS_METALLIB_RUNTIME_PACKAGE_ABI_VALIDATED
                reason = (
                    "Injected metallib bridge validated package ABI only; no GPU "
                    "execution or CPU-oracle output claim."
                )
            else:
                status = STATUS_METALLIB_RUNTIME_PACKAGE_FAILED
                reason = invocation.reason
    finally:
        if allocation_set is not None:
            allocation_set.release_all()

    return _result(
        status=status,
        artifact_dir=out_dir,
        package=package,
        native_buffer_runtime=native_buffer_runtime,
        invocation=invocation,
        matrix_write=matrix_write,
        cpu_comparison=comparison,
        allocation_set=allocation_set,
        reason=reason,
    )


__all__ = [
    "MetalMetallibRuntimeError",
    "MetalMetallibRuntimePackageResult",
    "STATUS_METALLIB_RUNTIME_PACKAGE_ABI_VALIDATED",
    "STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED",
    "STATUS_METALLIB_RUNTIME_PACKAGE_FAILED",
    "STATUS_SKIPPED_WITH_REASON",
    "run_metal_metallib_runtime_package",
]
