"""CPU-oracle comparison for Kernel IR Metal native readback.

This is the correctness check after a future Metal launch: read one native
output buffer with launch-plan dtype/shape metadata and compare it against the
CPU oracle. It does not launch a kernel and does not claim GPU execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pcc.kernel_ir.cpu_reference import CpuReferenceResult, Matrix
from pcc.kernel_ir.metal_buffer import MetalNativeBufferAllocationSet
from pcc.kernel_ir.metal_launch import MetalLaunchPlan
from pcc.kernel_ir.metal_tensor import MetalMatrixReadbackResult, read_metal_launch_matrix

STATUS_METAL_CPU_ORACLE_MATCH = "metal_cpu_oracle_match"


class MetalCpuOracleCompareError(ValueError):
    """Native Metal readback does not match the CPU oracle contract."""


@dataclass(frozen=True)
class MetalCpuOracleComparisonResult:
    """Comparison result for one native Metal output buffer vs CPU oracle."""

    status: str
    output_name: str
    shape: tuple[int, int]
    element_count: int
    max_abs_error: float
    atol: float
    rtol: float
    readback: MetalMatrixReadbackResult
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False
    reason: str = "Native Metal readback matches CPU oracle output."

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_name": self.output_name,
            "shape": list(self.shape),
            "element_count": self.element_count,
            "max_abs_error": self.max_abs_error,
            "atol": self.atol,
            "rtol": self.rtol,
            "readback": self.readback.to_dict(),
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
            "reason": self.reason,
        }


def _select_output(cpu_reference: CpuReferenceResult, output_name: str | None) -> tuple[str, Matrix]:
    outputs = cpu_reference.outputs
    if output_name is None:
        if len(outputs) != 1:
            raise MetalCpuOracleCompareError(
                f"CPU oracle has outputs {sorted(outputs)}; choose output_name="
            )
        name = next(iter(outputs))
        return name, outputs[name]
    if output_name not in outputs:
        raise MetalCpuOracleCompareError(
            f"CPU oracle has no output {output_name!r}; available {sorted(outputs)}"
        )
    return output_name, outputs[output_name]


def _shape(matrix: Matrix, *, name: str) -> tuple[int, int]:
    rows = len(matrix)
    if rows == 0:
        raise MetalCpuOracleCompareError(f"{name}: empty matrix")
    cols = len(matrix[0])
    if cols == 0:
        raise MetalCpuOracleCompareError(f"{name}: empty matrix row")
    for row_index, row in enumerate(matrix):
        if len(row) != cols:
            raise MetalCpuOracleCompareError(
                f"{name}: row {row_index} has {len(row)} columns, expected {cols}"
            )
    return rows, cols


def _compare_matrices(
    readback: Matrix,
    expected: Matrix,
    *,
    output_name: str,
    atol: float,
    rtol: float,
) -> tuple[tuple[int, int], int, float]:
    read_shape = _shape(readback, name=f"{output_name} readback")
    expected_shape = _shape(expected, name=f"{output_name} CPU oracle")
    if read_shape != expected_shape:
        raise MetalCpuOracleCompareError(
            f"{output_name}: readback shape {read_shape} != CPU oracle shape {expected_shape}"
        )
    max_abs_error = 0.0
    rows, cols = read_shape
    for row in range(rows):
        for col in range(cols):
            actual = float(readback[row][col])
            want = float(expected[row][col])
            abs_error = abs(actual - want)
            max_abs_error = max(max_abs_error, abs_error)
            if abs_error > atol + rtol * abs(want):
                raise MetalCpuOracleCompareError(
                    f"{output_name}[{row},{col}] mismatch: readback={actual!r}, "
                    f"cpu_oracle={want!r}, abs_error={abs_error!r}, "
                    f"tolerance={atol + rtol * abs(want)!r}"
                )
    return read_shape, rows * cols, max_abs_error


def verify_metal_launch_output_against_cpu_reference(
    library_path: str,
    allocation_set: MetalNativeBufferAllocationSet,
    launch_plan: MetalLaunchPlan,
    cpu_reference: CpuReferenceResult,
    *,
    output_name: str | None = None,
    atol: float = 1e-5,
    rtol: float = 1e-4,
    cdll_factory: Any | None = None,
    runtime_launch_executed: bool = False,
) -> MetalCpuOracleComparisonResult:
    """Read a native output matrix and compare it to the CPU oracle."""
    if atol < 0 or rtol < 0:
        raise MetalCpuOracleCompareError("comparison tolerances must be non-negative")
    selected_name, expected = _select_output(cpu_reference, output_name)
    readback = read_metal_launch_matrix(
        library_path,
        allocation_set,
        launch_plan,
        selected_name,
        cdll_factory=cdll_factory,
        runtime_launch_executed=runtime_launch_executed,
    )
    shape, element_count, max_abs_error = _compare_matrices(
        readback.matrix,
        expected,
        output_name=selected_name,
        atol=atol,
        rtol=rtol,
    )
    return MetalCpuOracleComparisonResult(
        status=STATUS_METAL_CPU_ORACLE_MATCH,
        output_name=selected_name,
        shape=shape,
        element_count=element_count,
        max_abs_error=max_abs_error,
        atol=atol,
        rtol=rtol,
        readback=readback,
        runtime_launch_executed=runtime_launch_executed,
        reason=(
            "Native Metal launch output matches CPU oracle output."
            if runtime_launch_executed
            else "Native Metal readback matches CPU oracle output."
        ),
    )


__all__ = [
    "MetalCpuOracleCompareError",
    "MetalCpuOracleComparisonResult",
    "STATUS_METAL_CPU_ORACLE_MATCH",
    "verify_metal_launch_output_against_cpu_reference",
]
