"""assist.py — CPU oracle + page classifier for GPU-participating marking.

The roadmap's key defensive idea: **do not offload arbitrary pointer graphs to
the GPU.** Route each page to one of three paths and keep a CPU oracle that can
reproduce the exact answer a GPU kernel *should* have produced, so kernel
parity is *provable against the oracle* rather than trusted:

* ``GPU_TRACEABLE``     — regular homogeneous layout (flat arrays, object
  vectors, raw payload). A data-parallel mark/scan kernel is a good fit.
* ``GPU_SUMMARY_ONLY``  — semi-regular (pointer tables): the GPU may compute a
  liveness *summary* (reduction/bitmap), but the authoritative frontier
  expansion stays on the CPU.
* ``CPU_ONLY``          — irregular polymorphic pointer graphs. Offloading loses
  to atomic contention (the classic GPU-GC failure mode); keep on CPU.

The oracle is deterministic: the same page always classifies the same way, and
``AssistOracle.mark_page`` computes the live-slot set on the CPU. A GPU kernel's
output would be checked by equality against ``mark_page``; if it diverges or the
kernel is unavailable, ``AssistOracle`` records a fallback and returns the CPU
answer. Fallback counts are the roadmap's required telemetry.

CLAIM BOUNDARY: this MODELS a GPU kernel's expected output on the CPU. It never
launches a kernel. "Kernel parity" here means "a future kernel must equal
``mark_page``"; it is a *specification*, not a measured GPU result.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from .substrate import LayoutClass, Page, PageState


class AssistClass(enum.Enum):
    """Routing decision for a page's tracing work."""

    GPU_TRACEABLE = "gpu_traceable"
    GPU_SUMMARY_ONLY = "gpu_summary_only"
    CPU_ONLY = "cpu_only"


# Deterministic layout -> assist-class routing table. This is the whole policy;
# keeping it a table (not scattered ``if``s) is what makes classification
# deterministic and auditable.
_ROUTING: Dict[LayoutClass, AssistClass] = {
    LayoutClass.FLAT_ARRAY: AssistClass.GPU_TRACEABLE,
    LayoutClass.OBJECT_VECTOR: AssistClass.GPU_TRACEABLE,
    LayoutClass.RAW_PAYLOAD: AssistClass.GPU_TRACEABLE,
    LayoutClass.IMMUTABLE: AssistClass.GPU_TRACEABLE,
    LayoutClass.POINTER_TABLE: AssistClass.GPU_SUMMARY_ONLY,
    LayoutClass.POINTER_GRAPH: AssistClass.CPU_ONLY,
}


def classify_page(page: Page) -> AssistClass:
    """Deterministically route a page to its tracing path by layout class.

    Pure function of ``page.layout``. Same input -> same output, always.
    """
    try:
        return _ROUTING[page.layout]
    except KeyError:  # pragma: no cover - defensive; enum is closed
        return AssistClass.CPU_ONLY


@dataclass
class AssistTelemetry:
    """Fallback-count telemetry, the roadmap's required operability signal."""

    classified: Dict[AssistClass, int] = field(
        default_factory=lambda: {c: 0 for c in AssistClass}
    )
    gpu_dispatched: int = 0
    cpu_fallbacks: int = 0
    parity_mismatches: int = 0
    kernel_unavailable: int = 0

    def as_dict(self) -> Dict[str, int]:
        out = {f"classified_{c.value}": n for c, n in self.classified.items()}
        out.update(
            gpu_dispatched=self.gpu_dispatched,
            cpu_fallbacks=self.cpu_fallbacks,
            parity_mismatches=self.parity_mismatches,
            kernel_unavailable=self.kernel_unavailable,
        )
        return out


class AssistOracle:
    """CPU oracle for GPU-assisted marking, plus a parity checker.

    ``mark_page`` is the authoritative CPU computation of a page's live slots.
    ``assisted_mark`` models the full decision: classify, (pretend to) dispatch
    to GPU for traceable/summary pages, then verify the (modelled) kernel output
    equals the oracle. A ``gpu_kernel`` callable may be supplied to simulate a
    kernel; if it is ``None`` (kernel unavailable) or its output disagrees, the
    oracle falls back to the CPU answer and increments telemetry.
    """

    def __init__(self) -> None:
        self.telemetry = AssistTelemetry()

    # -- oracle -------------------------------------------------------------

    @staticmethod
    def mark_page(page: Page) -> Set[int]:
        """Authoritative CPU liveness for a page: the set of live slot indices.

        The GPU kernel's contract is to return exactly this set. Modelled as a
        copy of the page's ``live_slots`` (in a real system this is where the
        bitmap scan / array trace would run). Only ALLOCATED pages are marked;
        others contribute nothing.
        """
        if page.state is not PageState.ALLOCATED:
            return set()
        return set(page.live_slots)

    # -- assisted path ------------------------------------------------------

    def assisted_mark(self, page: Page, gpu_kernel=None) -> Set[int]:
        """Route + (model) dispatch + verify parity. Returns the trusted set.

        ``gpu_kernel``: optional ``Callable[[Page], Set[int]]`` standing in for a
        device kernel. Its output is *never* trusted blindly — it is checked
        against :meth:`mark_page`. On mismatch or absence, the CPU oracle wins
        and telemetry records the fallback.
        """
        cls = classify_page(page)
        self.telemetry.classified[cls] += 1
        truth = self.mark_page(page)

        if cls is AssistClass.CPU_ONLY:
            # Never dispatched; CPU is authoritative by design.
            return truth

        # GPU_TRACEABLE / GPU_SUMMARY_ONLY are dispatch-eligible.
        if gpu_kernel is None:
            self.telemetry.kernel_unavailable += 1
            self.telemetry.cpu_fallbacks += 1
            return truth

        self.telemetry.gpu_dispatched += 1
        kernel_out = set(gpu_kernel(page))
        if kernel_out != truth:
            # Parity violation: the kernel disagreed with the oracle. Trust CPU.
            self.telemetry.parity_mismatches += 1
            self.telemetry.cpu_fallbacks += 1
            return truth
        return kernel_out

    def check_kernel_parity(self, page: Page, gpu_kernel) -> bool:
        """Return True iff the (modelled) kernel exactly matches the oracle.

        Pure check with no telemetry mutation, for use in parity assertions.
        """
        return set(gpu_kernel(page)) == self.mark_page(page)
