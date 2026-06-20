"""metal_adapter.py — NO-OP Metal unified-memory residency adapter interface.

Per the research doc, the Apple path is **not** CUDA: vLLM-Metal is MLX-based,
uses unified memory with "true zero-copy operations", and only has experimental
paged attention. So the Metal-specific strategy is NOT "port CUDA VMM" — it is
to keep one logical address space and treat pages as a **tiered residency
problem**: annotate each page with a residency class and let the adapter decide
what a real backend *would* do, without hard remapping.

This module is a **NO-OP adapter**: it records intended residency transitions
and telemetry but performs no device work. When Metal tooling is absent (always,
in this slice) residency operations return ``AdapterStatus.SKIPPED_WITH_REASON``
so callers can prove the fallback path rather than silently believing a device
op happened.

Residency modes (from the doc's classifier): CPU-hot, GPU-hot, shared-hot,
spillable, disabled-GPU.

CLAIM BOUNDARY: NO GPU, NO Metal, NO MLX. Detection is a stub that reports
absent; every "residency change" is a metadata note. This is the *interface*
a real adapter would implement, exercised as a CPU oracle.
"""
from __future__ import annotations

import enum
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .substrate import Page


class ResidencyMode(enum.Enum):
    """Where a page's payload is (intended to be) resident / hot."""

    CPU_HOT = "cpu_hot"
    GPU_HOT = "gpu_hot"
    SHARED_HOT = "shared_hot"     # unified-memory, hot on both — the Metal ideal
    SPILLABLE = "spillable"       # cold; eligible for host/SSD spill
    DISABLED_GPU = "disabled_gpu"  # GPU participation switched off for this page


class AdapterStatus(enum.Enum):
    """Outcome of a residency operation."""

    APPLIED = "applied"                       # a real backend would have acted
    SKIPPED_WITH_REASON = "skipped_with_reason"  # no-op; reason recorded
    REJECTED = "rejected"                     # illegal transition


@dataclass
class MetalTelemetry:
    """Residency telemetry — page age/hotness/residency/spill volume signals."""

    transitions: int = 0
    skipped: int = 0
    rejected: int = 0
    spill_events: int = 0
    reload_events: int = 0
    by_mode: Dict[ResidencyMode, int] = field(
        default_factory=lambda: {m: 0 for m in ResidencyMode}
    )
    skip_reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {
            "transitions": self.transitions,
            "skipped": self.skipped,
            "rejected": self.rejected,
            "spill_events": self.spill_events,
            "reload_events": self.reload_events,
        }
        out.update({f"mode_{m.value}": n for m, n in self.by_mode.items()})
        return out


def _metal_available() -> bool:
    """Stub Metal-tooling detector. Always reports absent in this slice.

    A real adapter would probe for the Metal framework / MLX. Here we only look
    for a wildly unlikely marker so the honest answer is False everywhere the
    oracle runs (no false "device present" claim).
    """
    # `metal` CLI or MLX are not what pcc ships; treat as always-absent oracle.
    return shutil.which("__pcc_fake_metal_probe_never_exists__") is not None


class MetalResidencyAdapter:
    """NO-OP unified-memory residency adapter (interface + CPU oracle).

    Holds an intended-residency map keyed by stable ``BlockId.key()``. Because
    no Metal tooling is present, every mutating op is a recorded no-op that
    returns ``SKIPPED_WITH_REASON``. The residency *intent* is still tracked so
    the collector's decisions are testable.
    """

    #: Legal residency transitions. DISABLED_GPU is terminal-ish: you may only
    #: move it back to CPU_HOT (re-enable on host) — modelling "GPU off".
    _LEGAL: Dict[ResidencyMode, Tuple[ResidencyMode, ...]] = {
        ResidencyMode.CPU_HOT: (
            ResidencyMode.GPU_HOT,
            ResidencyMode.SHARED_HOT,
            ResidencyMode.SPILLABLE,
            ResidencyMode.DISABLED_GPU,
        ),
        ResidencyMode.GPU_HOT: (
            ResidencyMode.CPU_HOT,
            ResidencyMode.SHARED_HOT,
            ResidencyMode.SPILLABLE,
            ResidencyMode.DISABLED_GPU,
        ),
        ResidencyMode.SHARED_HOT: (
            ResidencyMode.CPU_HOT,
            ResidencyMode.GPU_HOT,
            ResidencyMode.SPILLABLE,
            ResidencyMode.DISABLED_GPU,
        ),
        ResidencyMode.SPILLABLE: (
            ResidencyMode.CPU_HOT,
            ResidencyMode.GPU_HOT,
            ResidencyMode.SHARED_HOT,
            ResidencyMode.DISABLED_GPU,
        ),
        ResidencyMode.DISABLED_GPU: (
            ResidencyMode.CPU_HOT,
        ),
    }

    def __init__(self) -> None:
        self.telemetry = MetalTelemetry()
        self._residency: Dict[tuple, ResidencyMode] = {}
        self._available = _metal_available()

    @property
    def available(self) -> bool:
        return self._available

    def residency_of(self, page: Page) -> ResidencyMode:
        """Current intended residency; defaults CPU_HOT for a fresh page."""
        return self._residency.get(page.block_id.key(), ResidencyMode.CPU_HOT)

    def set_residency(
        self, page: Page, mode: ResidencyMode
    ) -> Tuple[AdapterStatus, Optional[str]]:
        """Request a residency transition. NO-OP; returns status + reason.

        Enforces the legal-transition table so an illegal request is rejected
        even though nothing is physically moved.
        """
        key = page.block_id.key()
        current = self._residency.get(key, ResidencyMode.CPU_HOT)
        if mode is not current and mode not in self._LEGAL.get(current, ()):  # illegal
            self.telemetry.rejected += 1
            return AdapterStatus.REJECTED, f"illegal transition {current.value}->{mode.value}"

        # Record the *intent* regardless (CPU-owned control plane truth).
        self._residency[key] = mode
        self.telemetry.by_mode[mode] += 1
        if mode is ResidencyMode.SPILLABLE:
            self.telemetry.spill_events += 1
        if current is ResidencyMode.SPILLABLE and mode is not ResidencyMode.SPILLABLE:
            self.telemetry.reload_events += 1

        if not self._available:
            self.telemetry.skipped += 1
            reason = "metal tooling absent; residency intent recorded, no device op"
            self.telemetry.skip_reasons.append(reason)
            return AdapterStatus.SKIPPED_WITH_REASON, reason

        # Unreachable in this slice (no tooling); the branch documents intent.
        self.telemetry.transitions += 1  # pragma: no cover
        return AdapterStatus.APPLIED, None  # pragma: no cover

    def snapshot(self) -> Dict[tuple, ResidencyMode]:
        """Copy of the intended-residency map (for tests / dashboards)."""
        return dict(self._residency)
