"""Shared contract for GPU external resource GC-backend seam tests."""

from __future__ import annotations

from pcc.gpu_gc.external_resource import ExternalResourceRegistry, ExternalResourceState
from pcc.kernel_ir.hmm_fence import BufferState, PccBufferHandle, PccFenceToken


def assert_external_resource_contract(backend: int) -> None:
    released: list[int] = []
    registry = ExternalResourceRegistry(gc_backend=backend)
    handle = PccBufferHandle(nbytes=128, dtype="f32", device="metal:0")
    fence = PccFenceToken()
    record = registry.register_buffer(
        handle,
        native_handle=0xC0FFEE + backend,
        release_callback=released.append,
    )

    registry.retain(record.resource_id)
    first = registry.release(record.resource_id, fence=fence)
    assert first.retain_count == 1
    assert first.release_executed is False
    assert handle.state is BufferState.LIVE
    assert registry.pending_count == 0

    second = registry.release(record.resource_id, fence=fence)
    assert second.retain_count == 0
    assert second.state is ExternalResourceState.PENDING_RELEASE
    assert handle.state is BufferState.PENDING_FREE

    before = registry.poll()
    assert before.gc_backend == backend
    assert before.released_resource_ids == ()
    assert before.pending_count == 1
    assert released == []

    fence.complete()
    after = registry.poll()
    assert after.gc_backend == backend
    assert after.released_resource_ids == (record.resource_id,)
    assert after.released_native_handles == (0xC0FFEE + backend,)
    assert after.pending_count == 0
    assert released == [0xC0FFEE + backend]
    assert handle.state is BufferState.FREED

    snapshot = registry.to_dict()
    assert snapshot["gc_backend"] == backend
    assert snapshot["whole_program_gpu"] is False
    assert snapshot["records"][0]["descriptor_contains_pyobject"] is False
    assert snapshot["records"][0]["release_executed"] is True
