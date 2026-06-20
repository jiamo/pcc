"""PCC HMM / fence model — buffer handles, packed args, deferred free.

Row K-P0-TVM-HMM-FENCE. This is the runtime-lifecycle side of the kernel
boundary. It is a **CPU-only state machine** in this first slice — no GPU, no
Metal command buffer, no torch/MPS. It models the *rules* that a real device
runtime must obey:

  * ``PccBufferHandle``  — a stable, opaque handle to a device/host buffer. The
    kernel IR sees this, never a PyObject*.
  * ``PccPackedArgs``    — the launcher ABI packet (POD scalars + DLPack-shaped
    buffer descriptors). Validated: NO GC-managed PyObject may be packed.
  * ``PccFenceToken``    — a completion point for one in-flight batch/command
    buffer. Free is gated on it.
  * ``PccDeferredFreeQueue`` — buffers scheduled for release are held until
    their fence completes, then reclaimed. Freeing before completion is a bug
    the queue refuses to commit (models Metal's "unretained reference before
    completion => undefined" rule).

The invariant this module proves (tested): **a buffer's release is DELAYED
until its fence completes; and the device IR never sees a GC-managed
PyObject** (DLPack/POD validation raises otherwise).

Importable standalone::

    from pcc.kernel_ir.hmm_fence import (
        PccBufferHandle, PccPackedArgs, PccFenceToken, PccDeferredFreeQueue,
    )
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from typing import Any


class HmmFenceError(ValueError):
    """An HMM/fence lifecycle invariant was violated."""


# POD dtypes accepted in a packed-args scalar slot. Mirrors ir.ScalarType by
# value so the two modules agree without importing each other.
_POD_SCALAR_DTYPES = frozenset(
    {"bool", "i8", "u8", "i16", "u16", "i32", "i64", "u32", "u64", "f16", "f32", "f64"}
)

_INTEGER_SCALAR_RANGES = {
    "i8": (-(1 << 7), (1 << 7) - 1),
    "u8": (0, (1 << 8) - 1),
    "i16": (-(1 << 15), (1 << 15) - 1),
    "u16": (0, (1 << 16) - 1),
    "i32": (-(1 << 31), (1 << 31) - 1),
    "u32": (0, (1 << 32) - 1),
    "i64": (-(1 << 63), (1 << 63) - 1),
    "u64": (0, (1 << 64) - 1),
}


_handle_ids = itertools.count(1)
_fence_ids = itertools.count(1)


class BufferState(enum.Enum):
    LIVE = "live"
    PENDING_FREE = "pending_free"  # release requested, waiting on a fence
    FREED = "freed"


@dataclass(eq=False)
class PccBufferHandle:
    """A stable, opaque handle to a buffer. NOT a PyObject reference.

    ``device`` names the owning device (Metal buffers may only be used with the
    device that created them — modeled by refusing cross-device packing).
    """

    nbytes: int
    dtype: str
    device: str = "cpu"
    handle_id: int = field(default_factory=lambda: next(_handle_ids))
    state: BufferState = BufferState.LIVE
    # The fence that must complete before this buffer's storage may be reclaimed.
    _guarding_fence: PccFenceToken | None = field(default=None, repr=False)

    def dlpack_descriptor(self) -> dict[str, Any]:
        """A DLPack-shaped POD descriptor (no live tensor, no PyObject)."""
        return {
            "handle_id": self.handle_id,
            "nbytes": self.nbytes,
            "dtype": self.dtype,
            "device": self.device,
        }


@dataclass(eq=False)
class PccFenceToken:
    """A completion point for one in-flight command batch.

    ``complete()`` is the causal edge the deferred-free queue waits on. Until it
    is called, any buffer guarded by this token stays PENDING_FREE.
    """

    fence_id: int = field(default_factory=lambda: next(_fence_ids))
    _completed: bool = False

    @property
    def completed(self) -> bool:
        return self._completed

    def complete(self) -> None:
        self._completed = True


def _is_gc_managed_pyobject(value: Any) -> bool:
    """True if *value* is a GC-managed host object that must not cross the
    device frontier. POD scalars (int/float/bool) and buffer handles are fine;
    everything with container/PyObject shape is not."""
    if isinstance(value, (bool, int, float)):
        return False
    if isinstance(value, PccBufferHandle):
        return False
    # list/dict/set/tuple/str/bytes and arbitrary objects are host escapes.
    return True


def _validate_pod_scalar_value(dtype: str, value: Any) -> None:
    if dtype == "bool":
        if type(value) is not bool:
            raise HmmFenceError("packed bool scalar must be a Python bool")
        return
    if dtype in _INTEGER_SCALAR_RANGES:
        if type(value) is not int:
            raise HmmFenceError(f"packed {dtype} scalar must be a Python int")
        lo, hi = _INTEGER_SCALAR_RANGES[dtype]
        if value < lo or value > hi:
            raise HmmFenceError(
                f"packed {dtype} scalar {value!r} is out of range "
                f"[{lo}, {hi}]"
            )
        return
    if dtype in {"f16", "f32", "f64"} and type(value) not in (int, float):
        raise HmmFenceError(f"packed {dtype} scalar must be numeric POD")


@dataclass
class PccPackedArgs:
    """The launcher ABI packet. Scalars are POD; buffers are handles.

    Validation (``validate``) raises if any packed value is a GC-managed
    PyObject, if a scalar dtype is not POD, or if a buffer is on a device other
    than the launch device.
    """

    scalars: list[tuple[str, Any]] = field(default_factory=list)
    buffers: list[PccBufferHandle] = field(default_factory=list)
    launch_device: str = "cpu"

    def add_scalar(self, dtype: str, value: Any) -> None:
        self.scalars.append((dtype, value))

    def add_buffer(self, handle: PccBufferHandle) -> None:
        self.buffers.append(handle)

    def validate(self) -> PccPackedArgs:
        for dtype, value in self.scalars:
            if dtype not in _POD_SCALAR_DTYPES:
                raise HmmFenceError(
                    f"packed scalar dtype {dtype!r} is not POD "
                    f"(accepted: {sorted(_POD_SCALAR_DTYPES)})"
                )
            if _is_gc_managed_pyobject(value):
                raise HmmFenceError(
                    f"packed scalar value {value!r} is a GC-managed PyObject; "
                    f"the device launcher ABI accepts POD scalars + buffer "
                    f"handles only — never a PyObject*."
                )
            _validate_pod_scalar_value(dtype, value)
        for buf in self.buffers:
            if not isinstance(buf, PccBufferHandle):
                raise HmmFenceError(
                    f"packed buffer {buf!r} is not a PccBufferHandle "
                    f"(a raw PyObject cannot be sunk into device IR)"
                )
            if buf.device != self.launch_device:
                raise HmmFenceError(
                    f"buffer {buf.handle_id} is on device {buf.device!r} but the "
                    f"launch device is {self.launch_device!r}; a buffer may only "
                    f"be used with the device that created it"
                )
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "launch_device": self.launch_device,
            "scalars": [{"dtype": d, "value": v} for d, v in self.scalars],
            "buffers": [b.dlpack_descriptor() for b in self.buffers],
        }


@dataclass
class PccDeferredFreeQueue:
    """Holds buffers scheduled for release until their fence completes.

    Contract:
      * ``schedule_free(buf, fence)`` marks the buffer PENDING_FREE and records
        the guarding fence. It does NOT free storage.
      * ``reclaim()`` frees every pending buffer whose fence has completed, and
        returns the list of reclaimed handle ids. Buffers whose fence is still
        in-flight stay pending.
      * A buffer is never FREED before its fence completes — that is the whole
        point (Metal: release before completion => undefined behavior).
    """

    _pending: list[PccBufferHandle] = field(default_factory=list)

    def schedule_free(self, buf: PccBufferHandle, fence: PccFenceToken) -> None:
        if buf.state == BufferState.FREED:
            raise HmmFenceError(f"buffer {buf.handle_id} is already freed")
        if not isinstance(fence, PccFenceToken):
            raise HmmFenceError("schedule_free requires a PccFenceToken")
        buf.state = BufferState.PENDING_FREE
        buf._guarding_fence = fence
        self._pending.append(buf)

    def reclaim(self) -> list[int]:
        """Free every pending buffer whose guarding fence has completed."""
        reclaimed: list[int] = []
        still_pending: list[PccBufferHandle] = []
        for buf in self._pending:
            fence = buf._guarding_fence
            if fence is not None and fence.completed:
                buf.state = BufferState.FREED
                buf._guarding_fence = None
                reclaimed.append(buf.handle_id)
            else:
                still_pending.append(buf)
        self._pending = still_pending
        return reclaimed

    @property
    def pending_count(self) -> int:
        return len(self._pending)


__all__ = [
    "HmmFenceError",
    "BufferState",
    "PccBufferHandle",
    "PccFenceToken",
    "PccPackedArgs",
    "PccDeferredFreeQueue",
]
