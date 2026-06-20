"""Fail-closed mapping from ds4 GPU lifetime APIs to pcc-owned concepts.

This module does not import, link, or execute ds4.  It inventories the bounded
``GPU Tensor and Command Lifetime`` section of ``ds4_gpu.h`` and maps it onto
pcc's own buffer, fence, readback, and deferred-free contracts.  The executable
part is a CPU-only lifecycle model; it submits no GPU command buffer.
"""

from __future__ import annotations

import enum
import hashlib
import itertools
import re
from dataclasses import dataclass, field

from pcc.kernel_ir.hmm_fence import (
    BufferState,
    PccBufferHandle,
    PccDeferredFreeQueue,
    PccFenceToken,
)


class Ds4GpuMappingError(ValueError):
    """The ds4 surface cannot be represented by the bounded pcc contract."""


@dataclass(frozen=True)
class Ds4GpuApiMapping:
    source_api: str
    pcc_owner: str
    operation: str


_MAPPINGS = (
    Ds4GpuApiMapping("ds4_gpu_init", "PccGpuDeviceContext", "open"),
    Ds4GpuApiMapping("ds4_gpu_cleanup", "PccGpuDeviceContext", "synchronize_close"),
    Ds4GpuApiMapping("ds4_gpu_tensor_alloc", "PccBufferHandle", "allocate_device"),
    Ds4GpuApiMapping("ds4_gpu_tensor_alloc_managed", "PccBufferHandle", "allocate_managed"),
    Ds4GpuApiMapping("ds4_gpu_tensor_view", "PccTensorSlice", "bounded_alias"),
    Ds4GpuApiMapping("ds4_gpu_tensor_free", "PccDeferredFreeQueue", "release_after_last_alias_and_fence"),
    Ds4GpuApiMapping("ds4_gpu_tensor_bytes", "PccTensorSlice", "query_nbytes"),
    Ds4GpuApiMapping("ds4_gpu_tensor_contents", "PccTensorSlice", "managed_host_access_guard"),
    Ds4GpuApiMapping("ds4_gpu_tensor_fill_f32", "KernelIRCommand", "record_fill"),
    Ds4GpuApiMapping("ds4_gpu_tensor_write", "PccTensorReadbackWindow", "bounded_host_to_device_copy"),
    Ds4GpuApiMapping("ds4_gpu_tensor_read", "PccTensorReadbackWindow", "completed_fence_readback"),
    Ds4GpuApiMapping("ds4_gpu_tensor_copy", "KernelIRCommand", "record_bounded_copy"),
    Ds4GpuApiMapping("ds4_gpu_tensor_copy_f32_to_f16", "KernelIRCommand", "record_checked_cast_copy"),
    Ds4GpuApiMapping("ds4_gpu_begin_commands", "PccCommandLifetime", "begin_recording"),
    Ds4GpuApiMapping("ds4_gpu_flush_commands", "PccFenceToken", "submit_batch_continue_recording"),
    Ds4GpuApiMapping("ds4_gpu_signal_selected_readback_ready", "PccFenceToken", "submit_selected_readback_event"),
    Ds4GpuApiMapping("ds4_gpu_commit_and_wait_selected_readback", "PccFenceToken", "complete_selected_event"),
    Ds4GpuApiMapping("ds4_gpu_wait_selected_readback_ready", "PccFenceToken", "complete_selected_event"),
    Ds4GpuApiMapping("ds4_gpu_tensor_read_after_selected_event", "PccTensorReadbackWindow", "event_gated_readback"),
    Ds4GpuApiMapping("ds4_gpu_end_commands", "PccCommandLifetime", "submit_and_end_recording"),
    Ds4GpuApiMapping("ds4_gpu_synchronize", "PccCommandLifetime", "complete_all_fences"),
)

DS4_GPU_LIFETIME_API_MAPPING = {item.source_api: item for item in _MAPPINGS}


@dataclass(frozen=True)
class Ds4GpuMappingReport:
    header_sha256: str
    source_apis: tuple[str, ...]
    mapped_apis: tuple[str, ...]
    classification: str = "PCC_OWNER_LIFECYCLE_MAPPING_ONLY"
    imports_or_links_ds4: bool = False
    executes_gpu: bool = False


def extract_ds4_gpu_lifetime_apis(header_source: str) -> tuple[str, ...]:
    """Extract only the first lifetime section, preserving declaration order."""
    title = "GPU Tensor and Command Lifetime."
    start = header_source.find(title)
    end = header_source.find("int ds4_gpu_set_model_map", start)
    if start < 0 or end < 0:
        raise Ds4GpuMappingError(
            "ds4_gpu.h lifetime section boundaries were not found; refusing "
            "to guess at a changed upstream API"
        )
    names = re.findall(r"\b(ds4_gpu_[A-Za-z0-9_]+)\s*\(", header_source[start:end])
    return tuple(dict.fromkeys(names))


def validate_ds4_gpu_lifetime_mapping(header_source: str) -> Ds4GpuMappingReport:
    """Require every source API and every mapping row to match exactly."""
    source_apis = extract_ds4_gpu_lifetime_apis(header_source)
    source_names = set(source_apis)
    mapped_names = set(DS4_GPU_LIFETIME_API_MAPPING)
    unmapped = sorted(source_names - mapped_names)
    stale = sorted(mapped_names - source_names)
    if unmapped or stale:
        raise Ds4GpuMappingError(
            f"ds4 GPU lifetime mapping drift: unmapped={unmapped}, stale={stale}"
        )
    return Ds4GpuMappingReport(
        header_sha256=hashlib.sha256(header_source.encode("utf-8")).hexdigest(),
        source_apis=source_apis,
        mapped_apis=tuple(DS4_GPU_LIFETIME_API_MAPPING),
    )


class PccTensorMemory(enum.Enum):
    DEVICE = "device"
    MANAGED = "managed"


class PccTensorSliceState(enum.Enum):
    LIVE = "live"
    RELEASED = "released"


_alias_ids = itertools.count(1)


@dataclass(eq=False)
class PccTensorSlice:
    """A bounded alias of pcc-owned storage, never a ds4 tensor owner."""

    buffer: PccBufferHandle
    byte_offset: int
    nbytes: int
    memory: PccTensorMemory
    owns_storage: bool
    alias_id: int = field(default_factory=lambda: next(_alias_ids))
    state: PccTensorSliceState = PccTensorSliceState.LIVE
    _owner_id: int = field(default=0, repr=False)

    def descriptor(self) -> dict[str, object]:
        if self.state is not PccTensorSliceState.LIVE:
            raise Ds4GpuMappingError(f"tensor alias {self.alias_id} is released")
        return {
            **self.buffer.dlpack_descriptor(),
            "byte_offset": self.byte_offset,
            "view_nbytes": self.nbytes,
            "memory": self.memory.value,
        }


@dataclass(frozen=True)
class PccTensorReadbackWindow:
    handle_id: int
    byte_offset: int
    nbytes: int
    event_value: int | None


@dataclass
class _StorageOwner:
    buffer: PccBufferHandle
    live_aliases: set[int]
    last_fence: PccFenceToken | None = None
    scheduled_for_free: bool = False


class _CommandState(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"


class Ds4ToPccLifetimeAdapter:
    """CPU-only proof that ds4-shaped lifetimes fit pcc ownership.

    Method names describe adapter actions, but storage and synchronization are
    owned by ``PccBufferHandle``/``PccFenceToken``.  No ds4 function is called.
    """

    def __init__(self, *, device: str = "metal:0") -> None:
        self.device = device
        self._owners: dict[int, _StorageOwner] = {}
        self._state = _CommandState.IDLE
        self._recorded_owner_ids: set[int] = set()
        self._fences: list[PccFenceToken] = []
        self._events: dict[int, PccFenceToken] = {}
        self._free_queue = PccDeferredFreeQueue()

    @property
    def pending_free_count(self) -> int:
        return self._free_queue.pending_count

    def allocate_tensor(
        self, nbytes: int, *, managed: bool = False
    ) -> PccTensorSlice:
        if type(nbytes) is not int or nbytes <= 0:
            raise Ds4GpuMappingError("tensor allocation size must be a positive int")
        buffer = PccBufferHandle(nbytes=nbytes, dtype="u8", device=self.device)
        tensor = PccTensorSlice(
            buffer=buffer,
            byte_offset=0,
            nbytes=nbytes,
            memory=PccTensorMemory.MANAGED if managed else PccTensorMemory.DEVICE,
            owns_storage=True,
            _owner_id=buffer.handle_id,
        )
        self._owners[buffer.handle_id] = _StorageOwner(buffer, {tensor.alias_id})
        return tensor

    def tensor_view(
        self, base: PccTensorSlice, *, offset: int, nbytes: int
    ) -> PccTensorSlice:
        owner = self._live_owner(base)
        self._validate_window(base, offset, nbytes)
        view = PccTensorSlice(
            buffer=base.buffer,
            byte_offset=base.byte_offset + offset,
            nbytes=nbytes,
            memory=base.memory,
            owns_storage=False,
            _owner_id=base._owner_id,
        )
        owner.live_aliases.add(view.alias_id)
        return view

    def begin_commands(self) -> None:
        if self._state is not _CommandState.IDLE:
            raise Ds4GpuMappingError("a pcc command lifetime is already recording")
        self._state = _CommandState.RECORDING
        self._recorded_owner_ids.clear()

    def record_tensor_use(self, tensor: PccTensorSlice) -> None:
        if self._state is not _CommandState.RECORDING:
            raise Ds4GpuMappingError("begin_commands is required before recording use")
        self._live_owner(tensor)
        self._recorded_owner_ids.add(tensor._owner_id)

    def flush_commands(self) -> PccFenceToken:
        return self._submit_recorded(continue_recording=True)

    def signal_selected_readback_ready(self) -> int:
        fence = self._submit_recorded(continue_recording=True)
        self._events[fence.fence_id] = fence
        return fence.fence_id

    def end_commands(self) -> PccFenceToken:
        return self._submit_recorded(continue_recording=False)

    def wait_selected_readback_ready(self, event_value: int) -> list[int]:
        fence = self._event_fence(event_value)
        fence.complete()
        return self._free_queue.reclaim()

    def commit_and_wait_selected_readback(self, event_value: int) -> list[int]:
        return self.wait_selected_readback_ready(event_value)

    def tensor_read_after_selected_event(
        self,
        tensor: PccTensorSlice,
        *,
        offset: int,
        nbytes: int,
        event_value: int,
    ) -> PccTensorReadbackWindow:
        self._live_owner(tensor)
        fence = self._event_fence(event_value)
        if not fence.completed:
            raise Ds4GpuMappingError(
                f"selected readback event {event_value} is not complete"
            )
        self._validate_window(tensor, offset, nbytes)
        return PccTensorReadbackWindow(
            handle_id=tensor.buffer.handle_id,
            byte_offset=tensor.byte_offset + offset,
            nbytes=nbytes,
            event_value=event_value,
        )

    def tensor_read(
        self, tensor: PccTensorSlice, *, offset: int, nbytes: int
    ) -> PccTensorReadbackWindow:
        owner = self._live_owner(tensor)
        if owner.last_fence is not None and not owner.last_fence.completed:
            raise Ds4GpuMappingError("tensor readback requires a completed fence")
        self._validate_window(tensor, offset, nbytes)
        return PccTensorReadbackWindow(
            handle_id=tensor.buffer.handle_id,
            byte_offset=tensor.byte_offset + offset,
            nbytes=nbytes,
            event_value=None,
        )

    def tensor_contents(self, tensor: PccTensorSlice) -> dict[str, object]:
        owner = self._live_owner(tensor)
        if tensor.memory is not PccTensorMemory.MANAGED:
            raise Ds4GpuMappingError("raw host contents require managed memory")
        if owner.last_fence is not None and not owner.last_fence.completed:
            raise Ds4GpuMappingError("managed host access requires a completed fence")
        return tensor.descriptor()

    def tensor_free(self, tensor: PccTensorSlice) -> None:
        owner = self._live_owner(tensor)
        if tensor._owner_id in self._recorded_owner_ids:
            raise Ds4GpuMappingError(
                "cannot release a tensor referenced by an unsubmitted command batch"
            )
        tensor.state = PccTensorSliceState.RELEASED
        owner.live_aliases.remove(tensor.alias_id)
        if owner.live_aliases or owner.scheduled_for_free:
            return
        fence = owner.last_fence or PccFenceToken()
        if owner.last_fence is None:
            fence.complete()
        self._free_queue.schedule_free(owner.buffer, fence)
        owner.scheduled_for_free = True

    def reclaim_completed(self) -> list[int]:
        return self._free_queue.reclaim()

    def synchronize(self) -> list[int]:
        if self._state is _CommandState.RECORDING:
            raise Ds4GpuMappingError("end_commands is required before synchronize")
        for fence in self._fences:
            fence.complete()
        return self._free_queue.reclaim()

    def _submit_recorded(self, *, continue_recording: bool) -> PccFenceToken:
        if self._state is not _CommandState.RECORDING:
            raise Ds4GpuMappingError("no pcc command lifetime is recording")
        fence = PccFenceToken()
        for owner_id in self._recorded_owner_ids:
            self._owners[owner_id].last_fence = fence
        self._recorded_owner_ids.clear()
        self._fences.append(fence)
        self._state = (
            _CommandState.RECORDING if continue_recording else _CommandState.IDLE
        )
        return fence

    def _event_fence(self, event_value: int) -> PccFenceToken:
        try:
            return self._events[event_value]
        except KeyError as exc:
            raise Ds4GpuMappingError(
                f"unknown selected readback event {event_value}"
            ) from exc

    def _live_owner(self, tensor: PccTensorSlice) -> _StorageOwner:
        if not isinstance(tensor, PccTensorSlice):
            raise Ds4GpuMappingError("expected a PccTensorSlice")
        if tensor.state is not PccTensorSliceState.LIVE:
            raise Ds4GpuMappingError(f"tensor alias {tensor.alias_id} is released")
        try:
            owner = self._owners[tensor._owner_id]
        except KeyError as exc:
            raise Ds4GpuMappingError("tensor is not owned by this pcc adapter") from exc
        if owner.buffer.state is not BufferState.LIVE:
            raise Ds4GpuMappingError(
                f"buffer {owner.buffer.handle_id} is not live: {owner.buffer.state.value}"
            )
        return owner

    @staticmethod
    def _validate_window(tensor: PccTensorSlice, offset: int, nbytes: int) -> None:
        if type(offset) is not int or type(nbytes) is not int:
            raise Ds4GpuMappingError("tensor byte window must use int offsets and sizes")
        if offset < 0 or nbytes <= 0 or offset + nbytes > tensor.nbytes:
            raise Ds4GpuMappingError(
                f"tensor byte window [{offset}, {offset + nbytes}) exceeds "
                f"alias size {tensor.nbytes}"
            )


__all__ = [
    "DS4_GPU_LIFETIME_API_MAPPING",
    "Ds4GpuApiMapping",
    "Ds4GpuMappingError",
    "Ds4GpuMappingReport",
    "Ds4ToPccLifetimeAdapter",
    "PccTensorMemory",
    "PccTensorReadbackWindow",
    "PccTensorSlice",
    "PccTensorSliceState",
    "extract_ds4_gpu_lifetime_apis",
    "validate_ds4_gpu_lifetime_mapping",
]
