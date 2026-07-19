"""harness.py — CPU-only blockized-GC surrogate measurement harness.

This module measures the **logical behavior of the CPU-only oracle** in
``pcc.gpu_gc``. It launches nothing on a device. Every number it reports is a
deterministic *logical counter* over the metadata model:

* mark / sweep logical **step counts** (how many pages the collector's tricolor
  worklist and sweep pass touched — a proxy for collector work, not time);
* **classification ratios** — how the substrate's pages route to
  ``GPU_TRACEABLE`` / ``GPU_SUMMARY_ONLY`` / ``CPU_ONLY`` under the deterministic
  ``pcc.gpu_gc.assist`` policy;
* **assist fallback telemetry** — ``gpu_dispatched`` / ``cpu_fallbacks`` /
  ``kernel_unavailable`` / ``parity_mismatches`` from ``AssistOracle``;
* **tiered block hit / recompute counts** — content-addressed reuse behavior
  from ``pcc.gpu_gc.tiered.BlockDirectory``.

It also runs an **LLM-serving surrogate stress**: a KV/attention-block churn
workload over ``tiered`` + ``substrate`` that reports *surrogate* pause / RSS /
fragmentation **counters** (logical step / page / span counts). These are named
with a ``_surrogate`` suffix to keep the boundary explicit.

Device-mode taxonomy
--------------------
``cpu-only`` RUNS (this file). ``cuda-assisted`` and ``metal-assisted`` are
reported ``SKIPPED_WITH_REASON`` because no device / kernel / Metal tooling is
present; the Metal skip is *proven* by asking the package's own
``MetalResidencyAdapter`` whether it detects tooling.

CLAIM BOUNDARY (repeat, load-bearing): measurement target only. NOT a completed
collector, NOT a throughput/latency/capacity claim, NOT a collector ranking. No
wall-clock is read anywhere in this module.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from pcc.gpu_gc import (
    AssistClass,
    AssistOracle,
    BlockDirectory,
    Color,
    CpuCollector,
    LayoutClass,
    MetalResidencyAdapter,
    Page,
    RegionKind,
    Substrate,
    classify_page,
    content_hash,
)


# ---------------------------------------------------------------------------
# Device-mode taxonomy
# ---------------------------------------------------------------------------

class DeviceMode(enum.Enum):
    """The three device modes this harness reasons about.

    Only ``CPU_ONLY`` runs; the two device modes are structurally skipped with a
    recorded reason so that "we did not measure a device" is an explicit,
    testable outcome rather than a silent omission.
    """

    CPU_ONLY = "cpu-only"
    CUDA_ASSISTED = "cuda-assisted"
    METAL_ASSISTED = "metal-assisted"


class SkipTaxonomy(enum.Enum):
    """Why a device mode is skipped. One reason per non-CPU mode."""

    RUNS = "runs"                          # cpu-only: measured here
    NO_CUDA_DEVICE = "no_cuda_device"      # cuda: no device / kernel in-repo
    NO_METAL_TOOLING = "no_metal_tooling"  # metal: MetalResidencyAdapter absent


@dataclass(frozen=True)
class ModeAvailability:
    """Availability verdict for one device mode."""

    mode: DeviceMode
    runs: bool
    taxonomy: SkipTaxonomy
    reason: str


def device_mode_availability() -> Dict[DeviceMode, ModeAvailability]:
    """Return the availability verdict for every device mode.

    ``cpu-only`` always runs. ``cuda-assisted`` is unconditionally skipped
    (there is no CUDA device or kernel in this repo slice). ``metal-assisted``
    is skipped iff the package's own ``MetalResidencyAdapter`` reports Metal
    tooling absent — we defer to the package so the skip reason is *proven*
    against the same detector the oracle uses, not asserted independently.
    """
    metal_available = MetalResidencyAdapter().available
    verdicts: Dict[DeviceMode, ModeAvailability] = {
        DeviceMode.CPU_ONLY: ModeAvailability(
            mode=DeviceMode.CPU_ONLY,
            runs=True,
            taxonomy=SkipTaxonomy.RUNS,
            reason="cpu-only surrogate; measured logical counters only",
        ),
        DeviceMode.CUDA_ASSISTED: ModeAvailability(
            mode=DeviceMode.CUDA_ASSISTED,
            runs=False,
            taxonomy=SkipTaxonomy.NO_CUDA_DEVICE,
            reason="no CUDA device or kernel in this slice; nothing to measure",
        ),
        DeviceMode.METAL_ASSISTED: ModeAvailability(
            mode=DeviceMode.METAL_ASSISTED,
            runs=(metal_available is True),
            taxonomy=(
                SkipTaxonomy.RUNS if metal_available else SkipTaxonomy.NO_METAL_TOOLING
            ),
            reason=(
                "metal tooling present"
                if metal_available
                else "MetalResidencyAdapter reports Metal/MLX tooling absent"
            ),
        ),
    }
    return verdicts


# ---------------------------------------------------------------------------
# Report dataclasses (all logical counters — no time, no bytes)
# ---------------------------------------------------------------------------

@dataclass
class SubstrateProfile:
    """Static shape of the substrate that was measured."""

    regions: int
    pages_allocated: int
    pages_by_layout: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return {
            "regions": self.regions,
            "pages_allocated": self.pages_allocated,
            "pages_by_layout": dict(self.pages_by_layout),
        }


@dataclass
class CollectorStepCounts:
    """Logical mark/sweep step counts for one collector cycle.

    ``mark_steps`` counts pages that were transitioned WHITE->…->BLACK (i.e.
    scanned by the tricolor worklist). ``sweep_steps`` counts ALLOCATED pages
    the sweep pass inspected. ``reclaimed`` counts pages actually reclaimed.
    These are collector-work proxies, NOT durations.
    """

    mark_steps: int
    sweep_steps: int
    reclaimed: int
    survivors: int

    def as_dict(self) -> Dict[str, int]:
        return {
            "mark_steps": self.mark_steps,
            "sweep_steps": self.sweep_steps,
            "reclaimed": self.reclaimed,
            "survivors": self.survivors,
        }


@dataclass
class ClassificationRatios:
    """Page classification counts + ratios under the deterministic assist policy."""

    counts: Dict[AssistClass, int]
    total: int

    def ratio(self, cls: AssistClass) -> float:
        if self.total == 0:
            return 0.0
        return self.counts.get(cls, 0) / self.total

    @property
    def gpu_traceable_ratio(self) -> float:
        return self.ratio(AssistClass.GPU_TRACEABLE)

    @property
    def cpu_only_ratio(self) -> float:
        return self.ratio(AssistClass.CPU_ONLY)

    @property
    def gpu_summary_ratio(self) -> float:
        return self.ratio(AssistClass.GPU_SUMMARY_ONLY)

    def as_dict(self) -> Dict[str, object]:
        return {
            "total": self.total,
            "counts": {c.value: n for c, n in self.counts.items()},
            "gpu_traceable_ratio": self.gpu_traceable_ratio,
            "gpu_summary_only_ratio": self.gpu_summary_ratio,
            "cpu_only_ratio": self.cpu_only_ratio,
        }


@dataclass
class AssistOracleReport:
    """Assist oracle telemetry after routing every page through the oracle."""

    classified: Dict[str, int]
    gpu_dispatched: int
    cpu_fallbacks: int
    kernel_unavailable: int
    parity_mismatches: int

    def as_dict(self) -> Dict[str, object]:
        return {
            "classified": dict(self.classified),
            "gpu_dispatched": self.gpu_dispatched,
            "cpu_fallbacks": self.cpu_fallbacks,
            "kernel_unavailable": self.kernel_unavailable,
            "parity_mismatches": self.parity_mismatches,
        }


@dataclass
class TieredReport:
    """Content-addressed tiered block-directory hit/recompute counters."""

    hits: int
    misses: int
    recomputes: int
    registrations: int
    invalidations: int
    entries: int

    def as_dict(self) -> Dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "recomputes": self.recomputes,
            "registrations": self.registrations,
            "invalidations": self.invalidations,
            "entries": self.entries,
        }


@dataclass
class ServingSurrogateCounters:
    """LLM-serving surrogate stress counters — ALL logical surrogates.

    * ``pause_surrogate_mark_steps`` — sum of mark steps across the collection
      cycles the workload triggered. Proxy for "pause work", NOT wall-clock.
    * ``rss_surrogate_peak_pages`` — peak count of simultaneously-ALLOCATED
      pages. Proxy for resident set, NOT bytes.
    * ``rss_surrogate_final_pages`` — ALLOCATED pages at end.
    * ``fragmentation_surrogate_free_spans`` — number of disjoint runs of
      free/evicted page indices at end (a gap-count proxy for fragmentation,
      NOT a byte ratio).
    * ``kv_reuse_hits`` / ``kv_recomputes`` — content-addressed KV-block reuse
      vs recompute, from the tiered directory.
    * ``blocks_touched`` / ``blocks_freed`` / ``collections`` — churn volume.
    """

    collections: int
    blocks_allocated: int
    blocks_touched: int
    blocks_freed: int
    pause_surrogate_mark_steps: int
    rss_surrogate_peak_pages: int
    rss_surrogate_final_pages: int
    fragmentation_surrogate_free_spans: int
    kv_reuse_hits: int
    kv_recomputes: int

    def as_dict(self) -> Dict[str, int]:
        return {
            "collections": self.collections,
            "blocks_allocated": self.blocks_allocated,
            "blocks_touched": self.blocks_touched,
            "blocks_freed": self.blocks_freed,
            "pause_surrogate_mark_steps": self.pause_surrogate_mark_steps,
            "rss_surrogate_peak_pages": self.rss_surrogate_peak_pages,
            "rss_surrogate_final_pages": self.rss_surrogate_final_pages,
            "fragmentation_surrogate_free_spans": self.fragmentation_surrogate_free_spans,
            "kv_reuse_hits": self.kv_reuse_hits,
            "kv_recomputes": self.kv_recomputes,
        }


@dataclass
class CpuOnlyBenchResult:
    """Full result bundle for the cpu-only substrate/collector/assist measurement."""

    profile: SubstrateProfile
    steps: CollectorStepCounts
    ratios: ClassificationRatios
    assist: AssistOracleReport
    tiered: TieredReport

    def as_dict(self) -> Dict[str, object]:
        return {
            "device_mode": DeviceMode.CPU_ONLY.value,
            "profile": self.profile.as_dict(),
            "steps": self.steps.as_dict(),
            "ratios": self.ratios.as_dict(),
            "assist": self.assist.as_dict(),
            "tiered": self.tiered.as_dict(),
        }


# ---------------------------------------------------------------------------
# Deterministic workload builders
# ---------------------------------------------------------------------------

# A fixed, deterministic mix of layout classes. Chosen so classification ratios
# are exact and auditable, and so both GPU-traceable and CPU-only pages exist.
# 4 GPU_TRACEABLE (FLAT_ARRAY, OBJECT_VECTOR, RAW_PAYLOAD, IMMUTABLE) +
# 1 GPU_SUMMARY_ONLY (POINTER_TABLE) + 1 CPU_ONLY (POINTER_GRAPH) per group.
_LAYOUT_CYCLE: Tuple[LayoutClass, ...] = (
    LayoutClass.FLAT_ARRAY,
    LayoutClass.OBJECT_VECTOR,
    LayoutClass.POINTER_TABLE,
    LayoutClass.POINTER_GRAPH,
    LayoutClass.RAW_PAYLOAD,
    LayoutClass.IMMUTABLE,
)


def build_classified_substrate(groups: int = 4) -> Tuple[Substrate, List[Page]]:
    """Build a deterministic substrate with a known layout-class mix.

    Returns the substrate and the list of allocated pages, in allocation order.
    Each group contributes exactly one page of each of the six layout classes,
    so ``len(_LAYOUT_CYCLE) * groups`` pages exist with an exactly-known
    classification ratio. All pages are in an OLD (non-moving) region except a
    small NURSERY region, mirroring the mostly-non-moving design.
    """
    if groups <= 0:
        raise ValueError("groups must be positive")
    sub = Substrate()
    per_region = len(_LAYOUT_CYCLE) * groups
    old = sub.add_region(RegionKind.OLD, capacity=per_region)
    pages: List[Page] = []
    serial_seed = 0
    for g in range(groups):
        for layout in _LAYOUT_CYCLE:
            content = None
            if layout is LayoutClass.IMMUTABLE:
                content = content_hash(f"immutable-{g}-{layout.value}".encode())
            pg = sub.allocate(old, layout, content_hash=content, epoch=1)
            # Give each page a deterministic, non-empty liveness bitmap so the
            # assist oracle has real slots to reproduce.
            pg.live_slots.update({0, 1, serial_seed % 3})
            pages.append(pg)
            serial_seed += 1
    return sub, pages


# ---------------------------------------------------------------------------
# The cpu-only measurement
# ---------------------------------------------------------------------------

def _measure_collector_steps(sub: Substrate, roots: List[Page]) -> CollectorStepCounts:
    """Run one full collector cycle and count logical mark/sweep steps.

    Mark steps: pages that ended BLACK (were scanned by the worklist). Sweep
    steps: ALLOCATED pages the sweep inspected (== live pages before sweep).
    """
    collector = CpuCollector(sub)
    for r in roots:
        collector.add_root(r)

    allocated_before = sum(
        1 for p in sub.pages() if p.state.value == "allocated"
    )

    collector.begin_cycle()
    collector.concurrent_mark()
    collector.remark()
    # Count marked pages BEFORE sweep mutates state.
    mark_steps = sum(
        1 for p in sub.pages() if collector.color_of(p.block_id.key()) is Color.BLACK
    )
    reclaimed = collector.sweep()
    collector.end_cycle()

    survivors = sum(1 for p in sub.pages() if p.state.value == "allocated")
    return CollectorStepCounts(
        mark_steps=mark_steps,
        sweep_steps=allocated_before,
        reclaimed=len(reclaimed),
        survivors=survivors,
    )


def _measure_classification(pages: List[Page]) -> ClassificationRatios:
    counts: Dict[AssistClass, int] = {c: 0 for c in AssistClass}
    for pg in pages:
        counts[classify_page(pg)] += 1
    return ClassificationRatios(counts=counts, total=len(pages))


def _measure_assist(pages: List[Page]) -> AssistOracleReport:
    """Route every page through the assist oracle with NO gpu kernel supplied.

    With no kernel, every dispatch-eligible page records ``kernel_unavailable``
    + ``cpu_fallbacks`` and the CPU oracle answer wins — the deterministic
    fallback path. CPU_ONLY pages are never dispatched.
    """
    oracle = AssistOracle()
    for pg in pages:
        oracle.assisted_mark(pg, gpu_kernel=None)
    t = oracle.telemetry
    return AssistOracleReport(
        classified={c.value: n for c, n in t.classified.items()},
        gpu_dispatched=t.gpu_dispatched,
        cpu_fallbacks=t.cpu_fallbacks,
        kernel_unavailable=t.kernel_unavailable,
        parity_mismatches=t.parity_mismatches,
    )


def _measure_tiered(pages: List[Page]) -> TieredReport:
    """Exercise content-addressed reuse over the IMMUTABLE pages.

    Registers each immutable page's content once, then re-requests the same
    content (hit, no recompute) and a never-seen content (miss -> recompute).
    Deterministic hit/recompute split.
    """
    directory = BlockDirectory()
    immutables = [p for p in pages if p.layout is LayoutClass.IMMUTABLE]
    # First pass: register (each unique content -> one registration).
    payloads: List[bytes] = []
    for i, _pg in enumerate(immutables):
        data = f"kv-block-{i}".encode()
        payloads.append(data)
        directory.register(data)
    # Reuse pass: same content -> hits, no recompute.
    for data in payloads:
        directory.get_or_recompute(data, recompute=lambda: b"RECOMPUTED")
    # Miss pass: unseen content -> recompute exactly once each.
    for i in range(len(payloads)):
        directory.get_or_recompute(f"unseen-{i}".encode(), recompute=lambda: b"NEW")
    s = directory.stats
    return TieredReport(
        hits=s.hits,
        misses=s.misses,
        recomputes=s.recomputes,
        registrations=s.registrations,
        invalidations=s.invalidations,
        entries=len(directory._entries),  # introspection for the measurement
    )


def run_cpu_only_bench(groups: int = 4) -> CpuOnlyBenchResult:
    """Run the full cpu-only measurement and return a deterministic result.

    Deterministic: identical ``groups`` -> identical counters every run. Nothing
    here reads a clock or allocates device memory.
    """
    sub, pages = build_classified_substrate(groups=groups)

    layout_hist: Dict[str, int] = {}
    for pg in pages:
        layout_hist[pg.layout.value] = layout_hist.get(pg.layout.value, 0) + 1
    profile = SubstrateProfile(
        regions=sum(1 for _ in sub.regions()),
        pages_allocated=len(pages),
        pages_by_layout=layout_hist,
    )

    # Roots: mark the GPU_TRACEABLE + summary pages as roots so CPU_ONLY
    # (POINTER_GRAPH) pages with no inbound reference get reclaimed — giving a
    # deterministic, nonzero reclaim count.
    roots = [p for p in pages if classify_page(p) is not AssistClass.CPU_ONLY]

    steps = _measure_collector_steps(sub, roots)
    # Re-derive classification/assist/tiered on a FRESH substrate: the sweep
    # above mutated page state, and classification/assist are page-shape
    # properties we want measured over the full original page set.
    _fresh_sub, fresh_pages = build_classified_substrate(groups=groups)
    ratios = _measure_classification(fresh_pages)
    assist = _measure_assist(fresh_pages)
    tiered = _measure_tiered(fresh_pages)

    return CpuOnlyBenchResult(
        profile=profile,
        steps=steps,
        ratios=ratios,
        assist=assist,
        tiered=tiered,
    )


# ---------------------------------------------------------------------------
# LLM-serving surrogate stress
# ---------------------------------------------------------------------------

def _count_free_spans(sub: Substrate) -> int:
    """Fragmentation surrogate: number of disjoint runs of non-allocated page
    indices across all regions. A larger span-count models a more fragmented
    heap. This is a gap-count proxy, NOT a byte ratio.
    """
    spans = 0
    for region in sub.regions():
        in_gap = False
        for i in range(region.capacity):
            pg = region.pages.get(i)
            is_free = pg is None or pg.state.value != "allocated"
            if is_free and not in_gap:
                spans += 1
                in_gap = True
            elif not is_free:
                in_gap = False
    return spans


def run_serving_surrogate(
    *,
    requests: int = 24,
    blocks_per_request: int = 4,
    shared_prefix_blocks: int = 2,
    collect_every: int = 6,
) -> ServingSurrogateCounters:
    """A deterministic KV/attention-block churn workload over tiered+substrate.

    Models LLM-serving pressure:

    * Each "request" allocates ``blocks_per_request`` KV pages in a NURSERY
      region (short-lived attention blocks) and registers a shared prompt
      *prefix* of ``shared_prefix_blocks`` content-addressed immutable KV blocks
      in the tiered directory. Repeated requests reuse the prefix (hit, no
      recompute) — the vLLM prefix-cache discipline.
    * Periodically (``collect_every`` requests) a collector cycle runs over the
      substrate, reclaiming KV pages whose request has "finished" (freed).
    * Counters are logical surrogates for pause/RSS/fragmentation — no clock,
      no bytes, no capacity claim.

    Deterministic: same arguments -> same counters.
    """
    if requests <= 0 or blocks_per_request <= 0:
        raise ValueError("requests and blocks_per_request must be positive")

    sub = Substrate()
    # Generous capacity so allocation never fails; NURSERY = short-lived KV.
    capacity = requests * blocks_per_request + shared_prefix_blocks + 8
    nursery = sub.add_region(RegionKind.NURSERY, capacity=capacity)
    directory = BlockDirectory()

    pause_surrogate = 0
    peak_pages = 0
    collections = 0
    blocks_allocated = 0
    blocks_touched = 0
    blocks_freed = 0

    # The shared prompt prefix (immutable, content-addressed). Registered once,
    # reused by every request.
    prefix_payloads = [f"prompt-prefix-block-{i}".encode() for i in range(shared_prefix_blocks)]

    live_request_pages: List[List[Page]] = []

    for req in range(requests):
        # Prefix reuse: first request registers; the rest hit (no recompute).
        for data in prefix_payloads:
            directory.get_or_recompute(data, recompute=lambda: b"PREFIX")
            blocks_touched += 1

        # Allocate this request's KV attention blocks.
        req_pages: List[Page] = []
        for b in range(blocks_per_request):
            pg = sub.allocate(nursery, LayoutClass.FLAT_ARRAY, epoch=req + 1)
            pg.live_slots.update({0, 1})
            req_pages.append(pg)
            blocks_allocated += 1
        live_request_pages.append(req_pages)

        # RSS surrogate: track peak simultaneously-allocated pages.
        allocated_now = sum(1 for p in sub.pages() if p.state.value == "allocated")
        peak_pages = max(peak_pages, allocated_now)

        # Finish the oldest request every couple of steps (free its KV pages).
        if len(live_request_pages) > 2:
            finished = live_request_pages.pop(0)
            for pg in finished:
                if pg.state.value == "allocated":
                    sub.free(pg)
                    blocks_freed += 1

        # Periodic collection cycle over the surviving KV pages.
        if (req + 1) % collect_every == 0:
            collector = CpuCollector(sub)
            for pages in live_request_pages:
                for pg in pages:
                    if pg.state.value == "allocated":
                        collector.add_root(pg)
            collector.begin_cycle()
            collector.concurrent_mark()
            collector.remark()
            marked = sum(
                1
                for p in sub.pages()
                if collector.color_of(p.block_id.key()) is Color.BLACK
            )
            pause_surrogate += marked
            collector.sweep()
            collector.end_cycle()
            collections += 1

    final_pages = sum(1 for p in sub.pages() if p.state.value == "allocated")
    frag_spans = _count_free_spans(sub)

    return ServingSurrogateCounters(
        collections=collections,
        blocks_allocated=blocks_allocated,
        blocks_touched=blocks_touched,
        blocks_freed=blocks_freed,
        pause_surrogate_mark_steps=pause_surrogate,
        rss_surrogate_peak_pages=peak_pages,
        rss_surrogate_final_pages=final_pages,
        fragmentation_surrogate_free_spans=frag_spans,
        kv_reuse_hits=directory.stats.hits,
        kv_recomputes=directory.stats.recomputes,
    )
