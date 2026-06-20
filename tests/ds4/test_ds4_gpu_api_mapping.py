from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from pcc.kernel_ir.ds4_gpu_mapping import (
    DS4_GPU_LIFETIME_API_MAPPING,
    Ds4GpuMappingError,
    Ds4ToPccLifetimeAdapter,
    PccTensorSliceState,
    validate_ds4_gpu_lifetime_mapping,
)
from pcc.kernel_ir.hmm_fence import BufferState


DEFAULT_DS4_ROOT = Path("~/pcc_refs/antirez-ds4-depth1").expanduser()
PINNED_COMMIT = "80ebbc396aee40eedc1d829222f3362d10fa4c6c"
PINNED_HEADER_SHA256 = "1a6c5760c10251250cf1838ac2452186e938e927070c5ce30471eeef9f49baa2"


@pytest.fixture(scope="module")
def ds4_gpu_header() -> str:
    root = Path(os.environ.get("PCC_DS4_ROOT", str(DEFAULT_DS4_ROOT))).expanduser()
    assert root.is_dir(), (
        f"pinned ds4 reference is required for the mapping gate: {root}; "
        "absence is not mapping evidence"
    )
    head = (root / ".git/HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        head = (root / ".git" / head.removeprefix("ref: ")).read_text(
            encoding="utf-8"
        ).strip()
    assert head == PINNED_COMMIT
    source = (root / "ds4_gpu.h").read_text(encoding="utf-8")
    assert hashlib.sha256(source.encode("utf-8")).hexdigest() == PINNED_HEADER_SHA256
    return source


def test_pinned_lifetime_section_has_complete_pcc_owner_mapping(ds4_gpu_header: str):
    report = validate_ds4_gpu_lifetime_mapping(ds4_gpu_header)
    assert report.header_sha256 == PINNED_HEADER_SHA256
    assert len(report.source_apis) == len(report.mapped_apis) == 21
    assert set(report.source_apis) == set(DS4_GPU_LIFETIME_API_MAPPING)
    assert report.classification == "PCC_OWNER_LIFECYCLE_MAPPING_ONLY"
    assert report.imports_or_links_ds4 is False
    assert report.executes_gpu is False
    assert all(
        item.pcc_owner.startswith(("Pcc", "KernelIR"))
        for item in DS4_GPU_LIFETIME_API_MAPPING.values()
    )


def test_lifetime_mapping_fails_closed_on_new_upstream_api(ds4_gpu_header: str):
    changed = ds4_gpu_header.replace(
        "int ds4_gpu_set_model_map",
        "int ds4_gpu_new_lifetime_api(void);\nint ds4_gpu_set_model_map",
        1,
    )
    with pytest.raises(Ds4GpuMappingError, match="unmapped=.*new_lifetime"):
        validate_ds4_gpu_lifetime_mapping(changed)


def test_tensor_views_are_aliases_and_last_release_waits_for_selected_fence():
    adapter = Ds4ToPccLifetimeAdapter(device="metal:0")
    root = adapter.allocate_tensor(256)
    view = adapter.tensor_view(root, offset=64, nbytes=96)
    assert view.buffer is root.buffer
    assert view.descriptor()["byte_offset"] == 64
    assert view.owns_storage is False

    adapter.begin_commands()
    adapter.record_tensor_use(view)
    event = adapter.signal_selected_readback_ready()
    adapter.tensor_free(root)
    assert root.state is PccTensorSliceState.RELEASED
    assert view.buffer.state is BufferState.LIVE
    assert adapter.pending_free_count == 0

    adapter.tensor_free(view)
    assert view.buffer.state is BufferState.PENDING_FREE
    assert adapter.reclaim_completed() == []
    assert adapter.wait_selected_readback_ready(event) == [view.buffer.handle_id]
    assert view.buffer.state is BufferState.FREED
    adapter.end_commands()


def test_selected_readback_requires_completed_event_and_is_range_checked():
    adapter = Ds4ToPccLifetimeAdapter()
    tensor = adapter.allocate_tensor(128, managed=True)
    adapter.begin_commands()
    adapter.record_tensor_use(tensor)
    event = adapter.signal_selected_readback_ready()

    with pytest.raises(Ds4GpuMappingError, match="not complete"):
        adapter.tensor_read_after_selected_event(
            tensor, offset=16, nbytes=32, event_value=event
        )
    adapter.commit_and_wait_selected_readback(event)
    window = adapter.tensor_read_after_selected_event(
        tensor, offset=16, nbytes=32, event_value=event
    )
    assert (window.handle_id, window.byte_offset, window.nbytes) == (
        tensor.buffer.handle_id,
        16,
        32,
    )
    assert adapter.tensor_contents(tensor)["memory"] == "managed"
    with pytest.raises(Ds4GpuMappingError, match="exceeds alias size"):
        adapter.tensor_read_after_selected_event(
            tensor, offset=120, nbytes=16, event_value=event
        )
    adapter.end_commands()
    adapter.tensor_free(tensor)
    assert adapter.reclaim_completed() == [tensor.buffer.handle_id]


def test_device_contents_and_unsubmitted_release_fail_closed():
    adapter = Ds4ToPccLifetimeAdapter()
    tensor = adapter.allocate_tensor(64)
    with pytest.raises(Ds4GpuMappingError, match="managed memory"):
        adapter.tensor_contents(tensor)
    adapter.begin_commands()
    adapter.record_tensor_use(tensor)
    with pytest.raises(Ds4GpuMappingError, match="unsubmitted"):
        adapter.tensor_free(tensor)
    fence = adapter.end_commands()
    adapter.tensor_free(tensor)
    assert adapter.reclaim_completed() == []
    assert fence.completed is False
    assert adapter.synchronize() == [tensor.buffer.handle_id]
