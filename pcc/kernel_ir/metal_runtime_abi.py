"""Pure runtime ABI records for Metal launcher calls.

This module deliberately avoids ``ctypes`` and host toolchain helpers. It
describes the ABI packet a pcc1/no-libpython runtime implementation must be
able to execute; CPython-hosted ``ctypes`` code is only one adapter for this
packet.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STATUS_METAL_SOURCE_RUNTIME_CALL_PLAN_READY = "metal_source_runtime_call_plan_ready"

_SCALAR_ABI_FORMATS = {
    "bool": ("<?", 1),
    "i8": ("<b", 1),
    "u8": ("<B", 1),
    "i16": ("<h", 2),
    "u16": ("<H", 2),
    "i32": ("<i", 4),
    "u32": ("<I", 4),
    "i64": ("<q", 8),
    "u64": ("<Q", 8),
    "f32": ("<f", 4),
    "f64": ("<d", 8),
}


@dataclass(frozen=True)
class MetalRuntimeBufferSlot:
    name: str
    kernel_index: int
    bridge_ordinal: int
    handle_id: int
    dtype: str
    native_mtlbuffer_ptr: int
    shape: tuple[int, ...] | None = None
    required_nbytes: int | None = None
    provided_nbytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "kernel_index": self.kernel_index,
            "bridge_ordinal": self.bridge_ordinal,
            "handle_id": self.handle_id,
            "dtype": self.dtype,
            "native_mtlbuffer_ptr": self.native_mtlbuffer_ptr,
        }
        if self.shape is not None:
            data["shape"] = list(self.shape)
        if self.required_nbytes is not None:
            data["required_nbytes"] = self.required_nbytes
        if self.provided_nbytes is not None:
            data["provided_nbytes"] = self.provided_nbytes
        return data


@dataclass(frozen=True)
class MetalRuntimeScalarSlot:
    name: str
    kernel_index: int
    dtype: str
    value: Any
    abi_offset: int
    abi_nbytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kernel_index": self.kernel_index,
            "dtype": self.dtype,
            "value": self.value,
            "abi_offset": self.abi_offset,
            "abi_nbytes": self.abi_nbytes,
        }


@dataclass(frozen=True)
class MetalSourceRuntimeCallPlan:
    status: str
    symbol: str
    bridge_library_path: str
    source_nbytes: int
    source_sha256: str
    scalar_payload_nbytes: int
    scalar_payload_sha256: str
    buffer_slots: tuple[MetalRuntimeBufferSlot, ...]
    scalar_slots: tuple[MetalRuntimeScalarSlot, ...]
    wait_until_completed: bool
    fence_callback_required: bool
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False
    claim_mode: str = "Metal runtime ABI call plan, not executed"
    source_bytes: bytes = field(repr=False, compare=False, default=b"")
    scalar_payload: bytes = field(repr=False, compare=False, default=b"")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "claim_mode": self.claim_mode,
            "symbol": self.symbol,
            "bridge_library_path": self.bridge_library_path,
            "source_nbytes": self.source_nbytes,
            "source_sha256": self.source_sha256,
            "scalar_payload_nbytes": self.scalar_payload_nbytes,
            "scalar_payload_sha256": self.scalar_payload_sha256,
            "buffer_slots": [slot.to_dict() for slot in self.buffer_slots],
            "scalar_slots": [slot.to_dict() for slot in self.scalar_slots],
            "wait_until_completed": self.wait_until_completed,
            "fence_callback_required": self.fence_callback_required,
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
        }


def _align_up(value: int, alignment: int) -> int:
    if alignment <= 1:
        return value
    return (value + alignment - 1) // alignment * alignment


def _pack_scalar(dtype: str, value: Any) -> tuple[bytes, int]:
    item = _SCALAR_ABI_FORMATS.get(dtype)
    if item is None:
        raise ValueError(f"unsupported runtime-source scalar dtype {dtype!r}")
    fmt, alignment = item
    return struct.pack(fmt, value), alignment


def build_metal_source_runtime_call_plan(
    *,
    launch_plan: Any,
    metal_source: str | bytes,
    bridge_library_path: str | Path,
    symbol: str,
    native_buffer_bindings: Any,
    wait_until_completed: bool = True,
) -> MetalSourceRuntimeCallPlan:
    """Build a pure ABI packet for one runtime-source Metal bridge call."""
    if not symbol:
        raise ValueError("runtime-source bridge symbol is empty")
    if not wait_until_completed:
        raise ValueError(
            "async runtime-source callback lifetime is not implemented; "
            "use wait_until_completed=True"
        )
    if not getattr(native_buffer_bindings, "native_buffer_handles_ready", False):
        raise ValueError("native MTLBuffer bindings are not ready")

    plan_buffers = [arg for arg in launch_plan.args if arg.kind == "buffer"]
    bindings = tuple(
        sorted(native_buffer_bindings.bindings, key=lambda binding: binding.bridge_ordinal)
    )
    expected = [(arg.name, arg.index, arg.handle_id) for arg in plan_buffers]
    actual = [
        (binding.name, binding.kernel_index, binding.handle_id)
        for binding in bindings
    ]
    if actual != expected:
        raise ValueError(
            f"native MTLBuffer bindings do not match launch plan: expected {expected}, got {actual}"
        )

    if isinstance(metal_source, str):
        source_bytes = metal_source.encode("utf-8")
    else:
        source_bytes = bytes(metal_source)
    if not source_bytes:
        raise ValueError("runtime-source Metal invocation requires non-empty source")

    buffer_slots = tuple(
        MetalRuntimeBufferSlot(
            name=binding.name,
            kernel_index=binding.kernel_index,
            bridge_ordinal=binding.bridge_ordinal,
            handle_id=binding.handle_id,
            dtype=binding.dtype,
            native_mtlbuffer_ptr=binding.native_mtlbuffer_ptr,
            shape=binding.shape,
            required_nbytes=binding.required_nbytes,
            provided_nbytes=binding.provided_nbytes,
        )
        for binding in bindings
    )
    scalar_slots = []
    scalar_payload = bytearray()
    for arg in launch_plan.args:
        if arg.kind != "scalar":
            continue
        payload, alignment = _pack_scalar(arg.dtype, arg.scalar_value)
        offset = _align_up(len(scalar_payload), alignment)
        if offset > len(scalar_payload):
            scalar_payload.extend(b"\x00" * (offset - len(scalar_payload)))
        scalar_payload.extend(payload)
        scalar_slots.append(
            MetalRuntimeScalarSlot(
                name=arg.name,
                kernel_index=arg.index,
                dtype=arg.dtype,
                value=arg.scalar_value,
                abi_offset=offset,
                abi_nbytes=len(payload),
            )
        )
    scalar_payload_bytes = bytes(scalar_payload)

    return MetalSourceRuntimeCallPlan(
        status=STATUS_METAL_SOURCE_RUNTIME_CALL_PLAN_READY,
        symbol=symbol,
        bridge_library_path=str(bridge_library_path),
        source_nbytes=len(source_bytes),
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        scalar_payload_nbytes=len(scalar_payload_bytes),
        scalar_payload_sha256=hashlib.sha256(scalar_payload_bytes).hexdigest(),
        buffer_slots=buffer_slots,
        scalar_slots=tuple(scalar_slots),
        wait_until_completed=True,
        fence_callback_required=False,
        source_bytes=source_bytes,
        scalar_payload=scalar_payload_bytes,
    )


__all__ = [
    "MetalRuntimeBufferSlot",
    "MetalRuntimeScalarSlot",
    "MetalSourceRuntimeCallPlan",
    "STATUS_METAL_SOURCE_RUNTIME_CALL_PLAN_READY",
    "build_metal_source_runtime_call_plan",
]
