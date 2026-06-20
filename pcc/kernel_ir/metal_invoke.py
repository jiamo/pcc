"""Strict host bridge invocation helpers for Kernel IR Metal packets.

This module is the call boundary after ``MetalBridgeInvocationPacket``. It
refuses to call the generated Objective-C bridge unless the packet is already
invocable: produced metallib path, validated bridge dylib symbol, and native
``id<MTLBuffer>`` bindings for every buffer slot. Tests may inject a fake CDLL
factory to validate ABI packing; injected calls are explicitly not GPU
execution claims.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pcc.kernel_ir.hmm_fence import PccFenceToken
from pcc.kernel_ir.metal_package import MetalBridgeInvocationPacket

STATUS_BRIDGE_INVOCATION_ABI_VALIDATED = "metal_bridge_invocation_abi_validated"
STATUS_BRIDGE_INVOKED = "metal_bridge_invoked"
STATUS_BRIDGE_INVOCATION_FAILED = "metal_bridge_invocation_failed"


class MetalBridgeInvocationError(ValueError):
    """A Metal bridge packet cannot be called safely."""


@dataclass(frozen=True)
class MetalBridgeInvocationResult:
    """Result of calling or ABI-validating a Metal host bridge packet."""

    status: str
    return_code: int
    bridge_function_called: bool
    fence_completed: bool
    injected_cdll_factory: bool
    runtime_launch_executed: bool
    whole_program_gpu: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "return_code": self.return_code,
            "bridge_function_called": self.bridge_function_called,
            "fence_completed": self.fence_completed,
            "injected_cdll_factory": self.injected_cdll_factory,
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
            "reason": self.reason,
        }


_FENCE_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_void_p)

_SCALAR_CTYPES: dict[str, type[ctypes._SimpleCData[Any]]] = {
    "bool": ctypes.c_bool,
    "i8": ctypes.c_int8,
    "u8": ctypes.c_uint8,
    "i16": ctypes.c_int16,
    "u16": ctypes.c_uint16,
    "i32": ctypes.c_int32,
    "u32": ctypes.c_uint32,
    "i64": ctypes.c_int64,
    "u64": ctypes.c_uint64,
    "f32": ctypes.c_float,
    "f64": ctypes.c_double,
}


def _preflight_bridge_packet(
    packet: MetalBridgeInvocationPacket,
    *,
    fence: PccFenceToken | None,
) -> None:
    if not packet.invocable:
        reasons = ", ".join(packet.not_ready_reasons) or "packet is not ready"
        raise MetalBridgeInvocationError(f"Metal bridge packet is not invocable: {reasons}")
    if not packet.metallib_path:
        raise MetalBridgeInvocationError("Metal bridge packet has no metallib path")
    if not Path(packet.metallib_path).is_file():
        raise MetalBridgeInvocationError(
            f"Metal bridge packet metallib is missing: {packet.metallib_path}"
        )
    if not Path(packet.bridge_library_path).is_file():
        raise MetalBridgeInvocationError(
            f"Metal bridge dylib is missing: {packet.bridge_library_path}"
        )
    if packet.fence_callback_required:
        if fence is None:
            raise MetalBridgeInvocationError(
                "Metal bridge invocation requires a PccFenceToken for completion"
            )
        if not packet.wait_until_completed:
            raise MetalBridgeInvocationError(
                "async Metal bridge callback lifetime is not implemented; "
                "build the packet with wait_until_completed=True"
            )


def _scalar_storage(slot: dict[str, Any]) -> ctypes._SimpleCData[Any]:
    dtype = slot.get("dtype")
    if dtype == "f16":
        raise MetalBridgeInvocationError(
            "f16 scalar invocation requires explicit IEEE-754 half packing"
        )
    ctype = _SCALAR_CTYPES.get(dtype)
    if ctype is None:
        raise MetalBridgeInvocationError(f"unsupported bridge scalar dtype {dtype!r}")
    return ctype(slot.get("scalar_value"))


def _load_bridge_function(
    packet: MetalBridgeInvocationPacket,
    *,
    cdll_factory: Callable[[str], Any] | None,
) -> Any:
    try:
        load_library = cdll_factory if cdll_factory is not None else ctypes.CDLL
        lib = load_library(packet.bridge_library_path)
    except OSError as exc:
        raise MetalBridgeInvocationError(f"Metal bridge dylib load failed: {exc}") from exc
    try:
        fn = getattr(lib, packet.symbol)
    except AttributeError as exc:
        raise MetalBridgeInvocationError(
            f"Metal bridge dylib does not export {packet.symbol!r}"
        ) from exc
    fn.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        _FENCE_CALLBACK,
        ctypes.c_void_p,
        ctypes.c_bool,
    ]
    fn.restype = ctypes.c_int64
    return fn


def invoke_metal_bridge_packet(
    packet: MetalBridgeInvocationPacket,
    *,
    fence: PccFenceToken | None = None,
    cdll_factory: Callable[[str], Any] | None = None,
) -> MetalBridgeInvocationResult:
    """Call a strict, already-ready Metal bridge packet.

    If ``cdll_factory`` is supplied, the call is an injected ABI validation. A
    zero return code from that path does not claim GPU execution.
    """
    _preflight_bridge_packet(packet, fence=fence)
    bridge_fn = _load_bridge_function(packet, cdll_factory=cdll_factory)

    buffer_ptrs = [
        ctypes.c_void_p(int(slot["native_mtlbuffer_ptr"]))
        for slot in packet.buffer_handle_slots
    ]
    buffer_array_type = ctypes.c_void_p * max(1, len(buffer_ptrs))
    buffer_array = buffer_array_type(*(buffer_ptrs or [ctypes.c_void_p()]))

    scalar_values = [_scalar_storage(slot) for slot in packet.scalar_value_slots]
    scalar_ptrs = [ctypes.cast(ctypes.byref(value), ctypes.c_void_p) for value in scalar_values]
    scalar_array_type = ctypes.c_void_p * max(1, len(scalar_ptrs))
    scalar_array = scalar_array_type(*(scalar_ptrs or [ctypes.c_void_p()]))

    def _complete(_ctx: ctypes.c_void_p) -> None:
        if fence is not None:
            fence.complete()

    callback = _FENCE_CALLBACK(_complete)
    rc = int(
        bridge_fn(
            packet.metallib_path.encode("utf-8"),
            buffer_array,
            scalar_array,
            callback,
            ctypes.c_void_p(0),
            ctypes.c_bool(packet.wait_until_completed),
        )
    )

    injected = cdll_factory is not None
    if rc == 0 and injected:
        return MetalBridgeInvocationResult(
            status=STATUS_BRIDGE_INVOCATION_ABI_VALIDATED,
            return_code=rc,
            bridge_function_called=True,
            fence_completed=fence.completed if fence is not None else False,
            injected_cdll_factory=True,
            runtime_launch_executed=False,
            reason="Injected CDLL bridge call validated ABI packing; no GPU execution claimed.",
        )
    if rc == 0:
        return MetalBridgeInvocationResult(
            status=STATUS_BRIDGE_INVOKED,
            return_code=rc,
            bridge_function_called=True,
            fence_completed=fence.completed if fence is not None else False,
            injected_cdll_factory=False,
            runtime_launch_executed=True,
            reason="Metal bridge returned success after strict invocation.",
        )
    return MetalBridgeInvocationResult(
        status=STATUS_BRIDGE_INVOCATION_FAILED,
        return_code=rc,
        bridge_function_called=True,
        fence_completed=fence.completed if fence is not None else False,
        injected_cdll_factory=injected,
        runtime_launch_executed=False,
        reason=f"Metal bridge returned non-zero rc={rc}; no successful launch claimed.",
    )


__all__ = [
    "MetalBridgeInvocationError",
    "MetalBridgeInvocationResult",
    "STATUS_BRIDGE_INVOCATION_ABI_VALIDATED",
    "STATUS_BRIDGE_INVOCATION_FAILED",
    "STATUS_BRIDGE_INVOKED",
    "invoke_metal_bridge_packet",
]
