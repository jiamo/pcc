"""Typed host matrix marshalling for Kernel IR Metal native buffers.

This module bridges CPU-oracle shaped matrices to the native MTLBuffer byte
runtime. It does not submit GPU work. The goal is to make the eventual Metal
launch comparable with the CPU oracle by proving the host can write input
matrices and read output matrices using the same launch-plan dtype/shape
metadata.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pcc.kernel_ir.metal_buffer import (
    MetalNativeBufferAllocation,
    MetalNativeBufferAllocationSet,
    MetalNativeBufferRuntimeError,
    read_metal_native_buffer,
    write_metal_native_buffer,
)
from pcc.kernel_ir.metal_launch import MetalLaunchPlan, MetalRuntimeArg

STATUS_METAL_MATRIX_BUFFERS_READY = "metal_matrix_buffers_ready"
STATUS_METAL_MATRIX_READBACK_VALIDATED = "metal_matrix_readback_validated"


class MetalTensorTransferError(ValueError):
    """A matrix could not be marshalled to or from native Metal buffers."""


Matrix = tuple[tuple[Any, ...], ...]


@dataclass(frozen=True)
class MetalMatrixTransfer:
    """One matrix write into a native Metal buffer."""

    name: str
    dtype: str
    shape: tuple[int, int]
    handle_id: int
    native_mtlbuffer_ptr: int
    nbytes: int
    zero_filled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "shape": list(self.shape),
            "handle_id": self.handle_id,
            "native_mtlbuffer_ptr": self.native_mtlbuffer_ptr,
            "nbytes": self.nbytes,
            "zero_filled": self.zero_filled,
        }


@dataclass(frozen=True)
class MetalMatrixTransferSet:
    """A set of matrices written into launch-plan native buffers."""

    status: str
    transfers: tuple[MetalMatrixTransfer, ...]
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False
    reason: str = "Host matrices copied into native MTLBuffers; no GPU work submitted."

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "transfers": [transfer.to_dict() for transfer in self.transfers],
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MetalMatrixReadbackResult:
    """One matrix read back from a native Metal buffer."""

    status: str
    name: str
    dtype: str
    shape: tuple[int, int]
    handle_id: int
    native_mtlbuffer_ptr: int
    matrix: Matrix
    nbytes: int
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False
    reason: str = "Native MTLBuffer matrix read back to host."

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "name": self.name,
            "dtype": self.dtype,
            "shape": list(self.shape),
            "handle_id": self.handle_id,
            "native_mtlbuffer_ptr": self.native_mtlbuffer_ptr,
            "matrix": [list(row) for row in self.matrix],
            "nbytes": self.nbytes,
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
            "reason": self.reason,
        }


_STRUCT_CODES = {
    "bool": "?",
    "i8": "b",
    "u8": "B",
    "i16": "h",
    "u16": "H",
    "i32": "i",
    "u32": "I",
    "i64": "q",
    "u64": "Q",
    "f16": "e",
    "f32": "f",
    "f64": "d",
}


def _shape2(shape: tuple[int, ...] | None, *, name: str) -> tuple[int, int]:
    if shape is None:
        raise MetalTensorTransferError(f"{name}: matrix transfer requires static shape")
    if len(shape) != 2:
        raise MetalTensorTransferError(f"{name}: matrix transfer requires rank-2 shape")
    rows, cols = shape
    if rows <= 0 or cols <= 0:
        raise MetalTensorTransferError(f"{name}: bad matrix shape {shape!r}")
    return rows, cols


def _struct_code(dtype: str) -> str:
    code = _STRUCT_CODES.get(dtype)
    if code is None:
        raise MetalTensorTransferError(f"unsupported matrix dtype {dtype!r}")
    return code


def _coerce_matrix(matrix: object, *, name: str, shape: tuple[int, int]) -> Matrix:
    rows, cols = shape
    if isinstance(matrix, (str, bytes)) or not isinstance(matrix, Sequence):
        raise MetalTensorTransferError(f"{name}: expected rank-2 matrix sequence")
    if len(matrix) != rows:
        raise MetalTensorTransferError(f"{name}: expected {rows} rows, got {len(matrix)}")
    out: list[tuple[Any, ...]] = []
    for row_index, row in enumerate(matrix):
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
            raise MetalTensorTransferError(f"{name}: row {row_index} is not a sequence")
        if len(row) != cols:
            raise MetalTensorTransferError(
                f"{name}: row {row_index} expected {cols} columns, got {len(row)}"
            )
        out.append(tuple(row))
    return tuple(out)


def pack_matrix_to_metal_bytes(
    matrix: object,
    *,
    dtype: str,
    shape: tuple[int, ...],
    name: str = "matrix",
) -> bytes:
    """Pack a row-major rank-2 matrix using launch-plan dtype metadata."""
    rows, cols = _shape2(shape, name=name)
    mat = _coerce_matrix(matrix, name=name, shape=(rows, cols))
    code = _struct_code(dtype)
    flat = [item for row in mat for item in row]
    try:
        return struct.pack("<" + code * len(flat), *flat)
    except (struct.error, TypeError, ValueError) as exc:
        raise MetalTensorTransferError(
            f"{name}: cannot pack {rows}x{cols} matrix as {dtype}"
        ) from exc


def unpack_matrix_from_metal_bytes(
    data: bytes,
    *,
    dtype: str,
    shape: tuple[int, ...],
    name: str = "matrix",
) -> Matrix:
    """Unpack a row-major rank-2 matrix using launch-plan dtype metadata."""
    rows, cols = _shape2(shape, name=name)
    code = _struct_code(dtype)
    count = rows * cols
    expected_nbytes = struct.calcsize("<" + code * count)
    if len(data) != expected_nbytes:
        raise MetalTensorTransferError(
            f"{name}: expected {expected_nbytes} bytes for {dtype}{shape}, got {len(data)}"
        )
    try:
        flat = struct.unpack("<" + code * count, data)
    except struct.error as exc:
        raise MetalTensorTransferError(f"{name}: cannot unpack {dtype} matrix") from exc
    return tuple(
        tuple(flat[row * cols + col] for col in range(cols))
        for row in range(rows)
    )


def _buffer_args_by_name(launch_plan: MetalLaunchPlan) -> dict[str, MetalRuntimeArg]:
    return {arg.name: arg for arg in launch_plan.args if arg.kind == "buffer"}


def _allocation_by_handle_id(
    allocation_set: MetalNativeBufferAllocationSet,
) -> dict[int, MetalNativeBufferAllocation]:
    if allocation_set.released:
        raise MetalTensorTransferError("native buffer allocation set has already been released")
    out: dict[int, MetalNativeBufferAllocation] = {}
    for allocation in allocation_set.allocations:
        out[allocation.handle_id] = allocation
    return out


def _allocation_for_arg(
    allocations: Mapping[int, MetalNativeBufferAllocation],
    arg: MetalRuntimeArg,
) -> MetalNativeBufferAllocation:
    if arg.handle_id is None:
        raise MetalTensorTransferError(f"{arg.name}: buffer arg has no handle id")
    allocation = allocations.get(arg.handle_id)
    if allocation is None:
        raise MetalTensorTransferError(
            f"{arg.name}: no native MTLBuffer allocation for handle {arg.handle_id}"
        )
    return allocation


def _zero_matrix(shape: tuple[int, int]) -> Matrix:
    rows, cols = shape
    return tuple(tuple(0 for _ in range(cols)) for _ in range(rows))


def write_metal_launch_matrices(
    library_path: str,
    allocation_set: MetalNativeBufferAllocationSet,
    launch_plan: MetalLaunchPlan,
    matrices: Mapping[str, object],
    *,
    zero_fill_unprovided: bool = False,
    cdll_factory: Any | None = None,
) -> MetalMatrixTransferSet:
    """Write host matrices into native launch buffers by argument name.

    ``zero_fill_unprovided`` is an explicit convenience for output buffers in
    the current GEMM path; it zeros every shaped buffer arg not present in
    ``matrices``. No GPU work is submitted.
    """
    buffer_args = _buffer_args_by_name(launch_plan)
    unknown = set(matrices) - set(buffer_args)
    if unknown:
        raise MetalTensorTransferError(f"matrix data supplied for non-buffer args {sorted(unknown)}")
    allocations = _allocation_by_handle_id(allocation_set)
    transfers: list[MetalMatrixTransfer] = []
    names = list(matrices)
    if zero_fill_unprovided:
        names.extend(name for name in buffer_args if name not in matrices)

    for name in names:
        arg = buffer_args[name]
        shape = _shape2(arg.shape, name=name)
        matrix = matrices[name] if name in matrices else _zero_matrix(shape)
        payload = pack_matrix_to_metal_bytes(matrix, dtype=arg.dtype, shape=arg.shape or (), name=name)
        if arg.required_nbytes is not None and len(payload) != arg.required_nbytes:
            raise MetalTensorTransferError(
                f"{name}: packed {len(payload)} bytes, expected {arg.required_nbytes}"
            )
        allocation = _allocation_for_arg(allocations, arg)
        if len(payload) > allocation.reported_nbytes:
            raise MetalTensorTransferError(
                f"{name}: packed {len(payload)} bytes exceeds native buffer size "
                f"{allocation.reported_nbytes}"
            )
        write_metal_native_buffer(
            library_path,
            allocation.native_mtlbuffer_ptr,
            payload,
            cdll_factory=cdll_factory,
        )
        transfers.append(
            MetalMatrixTransfer(
                name=name,
                dtype=arg.dtype,
                shape=shape,
                handle_id=allocation.handle_id,
                native_mtlbuffer_ptr=allocation.native_mtlbuffer_ptr,
                nbytes=len(payload),
                zero_filled=name not in matrices,
            )
        )
    return MetalMatrixTransferSet(
        status=STATUS_METAL_MATRIX_BUFFERS_READY,
        transfers=tuple(transfers),
    )


def read_metal_launch_matrix(
    library_path: str,
    allocation_set: MetalNativeBufferAllocationSet,
    launch_plan: MetalLaunchPlan,
    name: str,
    *,
    cdll_factory: Any | None = None,
    runtime_launch_executed: bool = False,
) -> MetalMatrixReadbackResult:
    """Read one launch-plan matrix buffer back to host by argument name."""
    buffer_args = _buffer_args_by_name(launch_plan)
    arg = buffer_args.get(name)
    if arg is None:
        raise MetalTensorTransferError(f"{name!r} is not a launch buffer arg")
    shape = _shape2(arg.shape, name=name)
    if arg.required_nbytes is None:
        raise MetalTensorTransferError(f"{name}: matrix readback requires static nbytes")
    allocation = _allocation_for_arg(_allocation_by_handle_id(allocation_set), arg)
    if arg.required_nbytes > allocation.reported_nbytes:
        raise MetalTensorTransferError(
            f"{name}: readback size {arg.required_nbytes} exceeds native buffer size "
            f"{allocation.reported_nbytes}"
        )
    read_result = read_metal_native_buffer(
        library_path,
        allocation.native_mtlbuffer_ptr,
        arg.required_nbytes,
        cdll_factory=cdll_factory,
    )
    if read_result.data is None:
        raise MetalTensorTransferError(f"{name}: native readback returned no data")
    matrix = unpack_matrix_from_metal_bytes(
        read_result.data,
        dtype=arg.dtype,
        shape=arg.shape or (),
        name=name,
    )
    return MetalMatrixReadbackResult(
        status=STATUS_METAL_MATRIX_READBACK_VALIDATED,
        name=name,
        dtype=arg.dtype,
        shape=shape,
        handle_id=allocation.handle_id,
        native_mtlbuffer_ptr=allocation.native_mtlbuffer_ptr,
        matrix=matrix,
        nbytes=arg.required_nbytes,
        runtime_launch_executed=runtime_launch_executed,
        reason=(
            "Native MTLBuffer matrix read back after a completed Metal launch."
            if runtime_launch_executed
            else "Native MTLBuffer matrix read back to host."
        ),
    )


__all__ = [
    "MetalMatrixReadbackResult",
    "MetalMatrixTransfer",
    "MetalMatrixTransferSet",
    "MetalTensorTransferError",
    "STATUS_METAL_MATRIX_BUFFERS_READY",
    "STATUS_METAL_MATRIX_READBACK_VALIDATED",
    "pack_matrix_to_metal_bytes",
    "read_metal_launch_matrix",
    "unpack_matrix_from_metal_bytes",
    "write_metal_launch_matrices",
]
