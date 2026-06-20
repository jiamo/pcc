"""Five-GC-visible external resource seam for GPU buffers and fences.

This is still a CPU-only state machine. It is the production-shaped boundary
that pcc's five GC backends can target uniformly: register an opaque device
resource, retain/release it through host ownership, and run the native release
callback only after the protecting ``PccFenceToken`` completes.

The resource record carries ``PccBufferHandle`` metadata only. It never stores a
PyObject payload for device traversal.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
import itertools
from typing import Any

from pcc.kernel_ir.hmm_fence import BufferState, PccBufferHandle, PccFenceToken


class ExternalResourceError(ValueError):
    """An external GPU resource lifecycle invariant was violated."""


class ExternalResourceState(Enum):
    LIVE = "live"
    PENDING_RELEASE = "pending_release"
    RELEASED = "released"


class ExternalResourceKind(Enum):
    GPU_BUFFER = "gpu_buffer"
    GPU_FENCE = "gpu_fence"


ReleaseCallback = Callable[[int], None]


_resource_ids = itertools.count(1)
_VALID_GC_BACKENDS = frozenset({0, 1, 2, 3, 4})


@dataclass
class ExternalResourceRecord:
    """One GC-visible opaque external resource."""

    resource_id: int
    kind: ExternalResourceKind
    buffer_handle: PccBufferHandle
    native_handle: int
    gc_backend: int
    release_callback: ReleaseCallback = field(repr=False)
    retain_count: int = 1
    state: ExternalResourceState = ExternalResourceState.LIVE
    pending_fence: PccFenceToken | None = field(default=None, repr=False)
    release_executed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "kind": self.kind.value,
            "buffer_handle": self.buffer_handle.dlpack_descriptor(),
            "native_handle": self.native_handle,
            "gc_backend": self.gc_backend,
            "retain_count": self.retain_count,
            "state": self.state.value,
            "pending_fence_id": (
                self.pending_fence.fence_id if self.pending_fence is not None else None
            ),
            "release_executed": self.release_executed,
            "descriptor_contains_pyobject": False,
            "whole_program_gpu": False,
        }


@dataclass(frozen=True)
class ExternalResourceReleaseResult:
    """Result of one host release request."""

    resource_id: int
    state: ExternalResourceState
    retain_count: int
    pending_fence_id: int | None
    release_executed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "state": self.state.value,
            "retain_count": self.retain_count,
            "pending_fence_id": self.pending_fence_id,
            "release_executed": self.release_executed,
            "whole_program_gpu": False,
        }


@dataclass(frozen=True)
class ExternalResourcePollResult:
    """Result of polling fence-completed external resource releases."""

    gc_backend: int
    released_resource_ids: tuple[int, ...]
    released_native_handles: tuple[int, ...]
    pending_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "gc_backend": self.gc_backend,
            "released_resource_ids": list(self.released_resource_ids),
            "released_native_handles": list(self.released_native_handles),
            "pending_count": self.pending_count,
            "whole_program_gpu": False,
        }


@dataclass
class ExternalResourceRegistry:
    """Uniform external-resource registry for GC backends 0..4."""

    gc_backend: int
    _records: dict[int, ExternalResourceRecord] = field(default_factory=dict, init=False, repr=False)
    _pending_release_ids: list[int] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.gc_backend not in _VALID_GC_BACKENDS:
            raise ExternalResourceError(
                f"external resource registry requires GC backend 0..4, got {self.gc_backend!r}"
            )

    def register_buffer(
        self,
        buffer_handle: PccBufferHandle,
        *,
        native_handle: int,
        release_callback: ReleaseCallback,
        payload: Any = None,
    ) -> ExternalResourceRecord:
        """Register an opaque GPU buffer as a GC-visible external resource."""

        if not isinstance(buffer_handle, PccBufferHandle):
            raise ExternalResourceError("external GPU buffer requires a PccBufferHandle")
        if buffer_handle.state is not BufferState.LIVE:
            raise ExternalResourceError("external GPU buffer handle must be live")
        if not isinstance(native_handle, int) or native_handle <= 0:
            raise ExternalResourceError("external GPU native handle must be a non-zero int")
        if not callable(release_callback):
            raise ExternalResourceError("external GPU resource requires a release callback")
        if payload is not None:
            raise ExternalResourceError(
                "external GPU resource records must not store PyObject payloads; "
                "device frontier carries PccBufferHandle metadata only"
            )

        resource_id = next(_resource_ids)
        record = ExternalResourceRecord(
            resource_id=resource_id,
            kind=ExternalResourceKind.GPU_BUFFER,
            buffer_handle=buffer_handle,
            native_handle=native_handle,
            gc_backend=self.gc_backend,
            release_callback=release_callback,
        )
        self._records[resource_id] = record
        return record

    def retain(self, resource_id: int) -> ExternalResourceRecord:
        record = self._live_record(resource_id)
        record.retain_count += 1
        return record

    def release(
        self,
        resource_id: int,
        *,
        fence: PccFenceToken,
    ) -> ExternalResourceReleaseResult:
        if not isinstance(fence, PccFenceToken):
            raise ExternalResourceError("external GPU resource release requires a PccFenceToken")
        record = self._live_record(resource_id)
        if record.retain_count <= 0:
            raise ExternalResourceError(f"external resource {resource_id} has no retains")

        record.retain_count -= 1
        if record.retain_count > 0:
            return ExternalResourceReleaseResult(
                resource_id=record.resource_id,
                state=record.state,
                retain_count=record.retain_count,
                pending_fence_id=None,
            )

        record.state = ExternalResourceState.PENDING_RELEASE
        record.pending_fence = fence
        record.buffer_handle.state = BufferState.PENDING_FREE
        record.buffer_handle._guarding_fence = fence
        self._pending_release_ids.append(record.resource_id)
        return ExternalResourceReleaseResult(
            resource_id=record.resource_id,
            state=record.state,
            retain_count=record.retain_count,
            pending_fence_id=fence.fence_id,
        )

    def poll(self) -> ExternalResourcePollResult:
        released_ids: list[int] = []
        released_native_handles: list[int] = []
        still_pending: list[int] = []

        for resource_id in self._pending_release_ids:
            record = self._records.get(resource_id)
            if record is None:
                continue
            fence = record.pending_fence
            if fence is not None and fence.completed:
                record.release_callback(record.native_handle)
                record.release_executed = True
                record.state = ExternalResourceState.RELEASED
                record.pending_fence = None
                record.buffer_handle.state = BufferState.FREED
                record.buffer_handle._guarding_fence = None
                released_ids.append(resource_id)
                released_native_handles.append(record.native_handle)
            else:
                still_pending.append(resource_id)

        self._pending_release_ids = still_pending
        return ExternalResourcePollResult(
            gc_backend=self.gc_backend,
            released_resource_ids=tuple(released_ids),
            released_native_handles=tuple(released_native_handles),
            pending_count=len(self._pending_release_ids),
        )

    def record(self, resource_id: int) -> ExternalResourceRecord:
        try:
            return self._records[resource_id]
        except KeyError as exc:
            raise ExternalResourceError(f"unknown external resource {resource_id}") from exc

    @property
    def pending_count(self) -> int:
        return len(self._pending_release_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gc_backend": self.gc_backend,
            "pending_count": self.pending_count,
            "records": [
                record.to_dict()
                for record in sorted(self._records.values(), key=lambda item: item.resource_id)
            ],
            "whole_program_gpu": False,
        }

    def _live_record(self, resource_id: int) -> ExternalResourceRecord:
        record = self.record(resource_id)
        if record.state is ExternalResourceState.RELEASED:
            raise ExternalResourceError(f"external resource {resource_id} is already released")
        if record.state is ExternalResourceState.PENDING_RELEASE:
            raise ExternalResourceError(
                f"external resource {resource_id} is already pending release"
            )
        return record


__all__ = [
    "ExternalResourceError",
    "ExternalResourceKind",
    "ExternalResourcePollResult",
    "ExternalResourceRecord",
    "ExternalResourceRegistry",
    "ExternalResourceReleaseResult",
    "ExternalResourceState",
]
