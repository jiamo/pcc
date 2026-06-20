"""CPU-only oracle tests for pcc.gpu_gc.external_resource."""

from __future__ import annotations

import pytest

from pcc.gpu_gc.external_resource import (
    ExternalResourceError,
    ExternalResourceRegistry,
    ExternalResourceState,
)
from pcc.kernel_ir.hmm_fence import BufferState, PccBufferHandle, PccFenceToken


def test_external_resource_release_waits_for_fence_completion():
    released: list[int] = []
    handle = PccBufferHandle(nbytes=64, dtype="f32", device="metal:0")
    fence = PccFenceToken()
    registry = ExternalResourceRegistry(gc_backend=4)
    record = registry.register_buffer(
        handle,
        native_handle=0xABC000,
        release_callback=released.append,
    )

    result = registry.release(record.resource_id, fence=fence)
    before = registry.poll()

    assert result.state is ExternalResourceState.PENDING_RELEASE
    assert handle.state is BufferState.PENDING_FREE
    assert before.released_resource_ids == ()
    assert before.pending_count == 1
    assert released == []

    fence.complete()
    after = registry.poll()

    assert after.released_resource_ids == (record.resource_id,)
    assert after.released_native_handles == (0xABC000,)
    assert after.pending_count == 0
    assert released == [0xABC000]
    assert handle.state is BufferState.FREED
    assert registry.record(record.resource_id).state is ExternalResourceState.RELEASED


def test_external_resource_retain_delays_last_release_only():
    released: list[int] = []
    handle = PccBufferHandle(nbytes=32, dtype="i32", device="metal:0")
    fence = PccFenceToken()
    registry = ExternalResourceRegistry(gc_backend=2)
    record = registry.register_buffer(
        handle,
        native_handle=0xFA1000,
        release_callback=released.append,
    )
    registry.retain(record.resource_id)

    first = registry.release(record.resource_id, fence=fence)
    assert first.retain_count == 1
    assert first.pending_fence_id is None
    assert registry.poll().pending_count == 0
    assert released == []
    assert handle.state is BufferState.LIVE

    second = registry.release(record.resource_id, fence=fence)
    assert second.retain_count == 0
    assert second.pending_fence_id == fence.fence_id
    fence.complete()
    registry.poll()
    registry.poll()

    assert released == [0xFA1000]


def test_external_resource_rejects_pyobject_payload_and_bad_backend():
    with pytest.raises(ExternalResourceError, match="backend 0..4"):
        ExternalResourceRegistry(gc_backend=5)

    registry = ExternalResourceRegistry(gc_backend=0)
    with pytest.raises(ExternalResourceError, match="must not store PyObject payloads"):
        registry.register_buffer(
            PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"),
            native_handle=0xABC,
            release_callback=lambda _native: None,
            payload={"host": "object"},
        )


def test_external_resource_registry_dict_is_device_frontier_safe():
    registry = ExternalResourceRegistry(gc_backend=1)
    record = registry.register_buffer(
        PccBufferHandle(nbytes=8, dtype="u32", device="metal:0"),
        native_handle=0xBEEF,
        release_callback=lambda _native: None,
    )
    data = registry.to_dict()

    assert data["gc_backend"] == 1
    assert data["whole_program_gpu"] is False
    assert data["records"][0]["resource_id"] == record.resource_id
    assert data["records"][0]["descriptor_contains_pyobject"] is False
    assert data["records"][0]["whole_program_gpu"] is False
