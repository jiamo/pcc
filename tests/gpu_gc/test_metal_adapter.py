"""CPU-only oracle tests for pcc.gpu_gc.metal_adapter.

This is a NO-OP adapter: no Metal/MLX tooling is present, so every residency
op returns SKIPPED_WITH_REASON while still recording CPU-owned residency intent.
Illegal transitions are rejected. Telemetry aggregates spill/reload/skip.
"""
from __future__ import annotations

from pcc.gpu_gc.metal_adapter import (
    AdapterStatus,
    MetalResidencyAdapter,
    ResidencyMode,
)
from pcc.gpu_gc.substrate import LayoutClass, RegionKind, Substrate


def _page():
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 8)
    return sub, old.pages, sub.allocate(old, LayoutClass.FLAT_ARRAY)


def test_adapter_reports_metal_absent():
    ad = MetalResidencyAdapter()
    assert ad.available is False


def test_default_residency_is_cpu_hot():
    _, _, p = _page()
    ad = MetalResidencyAdapter()
    assert ad.residency_of(p) is ResidencyMode.CPU_HOT


def test_residency_op_is_noop_skipped_with_reason():
    _, _, p = _page()
    ad = MetalResidencyAdapter()
    status, reason = ad.set_residency(p, ResidencyMode.GPU_HOT)
    assert status is AdapterStatus.SKIPPED_WITH_REASON
    assert reason and "metal tooling absent" in reason
    # Intent is still recorded despite the no-op.
    assert ad.residency_of(p) is ResidencyMode.GPU_HOT
    assert ad.telemetry.transitions == 0
    assert ad.telemetry.skipped == 1


def test_all_modes_reachable_from_cpu_hot():
    for mode in (
        ResidencyMode.GPU_HOT,
        ResidencyMode.SHARED_HOT,
        ResidencyMode.SPILLABLE,
        ResidencyMode.DISABLED_GPU,
    ):
        _, _, p = _page()
        ad = MetalResidencyAdapter()
        status, _ = ad.set_residency(p, mode)
        assert status is AdapterStatus.SKIPPED_WITH_REASON
        assert ad.residency_of(p) is mode


def test_disabled_gpu_only_returns_to_cpu_hot():
    _, _, p = _page()
    ad = MetalResidencyAdapter()
    ad.set_residency(p, ResidencyMode.DISABLED_GPU)
    # Illegal: DISABLED_GPU -> GPU_HOT
    status, reason = ad.set_residency(p, ResidencyMode.GPU_HOT)
    assert status is AdapterStatus.REJECTED
    assert "illegal transition" in reason
    assert ad.telemetry.rejected == 1
    # Legal: DISABLED_GPU -> CPU_HOT (re-enable on host)
    status2, _ = ad.set_residency(p, ResidencyMode.CPU_HOT)
    assert status2 is AdapterStatus.SKIPPED_WITH_REASON
    assert ad.residency_of(p) is ResidencyMode.CPU_HOT


def test_spill_and_reload_telemetry():
    _, _, p = _page()
    ad = MetalResidencyAdapter()
    ad.set_residency(p, ResidencyMode.SPILLABLE)   # spill event
    ad.set_residency(p, ResidencyMode.GPU_HOT)     # reload event
    assert ad.telemetry.spill_events == 1
    assert ad.telemetry.reload_events == 1


def test_same_mode_is_idempotent_not_rejected():
    _, _, p = _page()
    ad = MetalResidencyAdapter()
    ad.set_residency(p, ResidencyMode.GPU_HOT)
    status, _ = ad.set_residency(p, ResidencyMode.GPU_HOT)
    assert status is AdapterStatus.SKIPPED_WITH_REASON  # not rejected


def test_snapshot_and_telemetry_dict():
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 4)
    p0 = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    p1 = sub.allocate(old, LayoutClass.OBJECT_VECTOR)
    ad = MetalResidencyAdapter()
    ad.set_residency(p0, ResidencyMode.GPU_HOT)
    ad.set_residency(p1, ResidencyMode.SPILLABLE)
    snap = ad.snapshot()
    assert snap[p0.block_id.key()] is ResidencyMode.GPU_HOT
    assert snap[p1.block_id.key()] is ResidencyMode.SPILLABLE
    d = ad.telemetry.as_dict()
    assert d["mode_gpu_hot"] == 1
    assert d["mode_spillable"] == 1
    assert d["skipped"] == 2
