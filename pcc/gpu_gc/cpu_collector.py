"""cpu_collector.py — CPU-first partitioned concurrent collector baseline.

This is the roadmap's "CPU-first concurrent PCC baseline with no GPU
dependency". It is a *model*: it operates over the metadata substrate
(regions/pages, live-slot sets, remembered sets) and proves that the
concurrent-marking state machine **preserves reachability** across mutator
activity, epoch transitions, and selective evacuation — entirely on the CPU.

SATB vs incremental-update: WE CHOOSE **SATB** (snapshot-at-the-beginning).
--------------------------------------------------------------------------
Rationale, tied to the research doc's constraints:

* The design is **mostly non-moving** with a **CPU-owned control plane** and a
  **GPU data plane** that scans stable metadata. SATB fixes the reachable set
  as a *snapshot* at mark-start: anything live at snapshot stays live this
  cycle (it may float, and is reclaimed next cycle). That gives the GPU scan a
  **stable frontier** — the set of objects the device must consider does not
  grow due to concurrent mutation, so a device kernel can be launched over a
  fixed work list without re-synchronising with the mutator mid-scan.
* Incremental-update (Dijkstra-style) instead re-greys the *target* of a
  pointer write, which can *add* work to the mark frontier while the GPU is
  already scanning it — exactly the "surprise growth under a running kernel"
  hazard the doc warns against (the Unified-Shared-Memory paper exists because
  GC/accelerator interop breaks when the reachable set shifts under device
  execution).
* SATB's cost is a **write barrier that shades the OVERWRITTEN (old) value**,
  which is cheap and local, and some floating garbage. For a
  bandwidth-bound GPU-assisted scan we prefer a stable frontier + a little
  float over a moving frontier + zero float. This matches ZGC/G1-style
  concurrent collectors (SATB) more than CMS incremental-update.

Barrier: :class:`BarrierKind.SATB_DELETE` — on a pointer store ``slot = new``
we shade the *previous* referent grey before it is lost. (An
``INCREMENTAL_UPDATE`` value is provided for comparison/telemetry only; the
default and the tested path is SATB.)

CLAIM BOUNDARY: CPU-only *model* of a concurrent collector. No real threads,
no GPU, no relocation of real objects — "evacuation" copies page metadata to
OLD pages, remaps model roots/remembered sets, and retires the nursery source.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .substrate import (
    LayoutClass,
    Page,
    PageState,
    Region,
    RegionKind,
    Substrate,
)


class CollectorError(RuntimeError):
    """Raised on an illegal collector-cycle transition."""


class Color(enum.Enum):
    """Tricolor marking color of a page (page-granular in this model)."""

    WHITE = "white"   # not yet reached; candidate for reclamation
    GREY = "grey"     # reached, children not yet scanned
    BLACK = "black"   # reached and fully scanned


class BarrierKind(enum.Enum):
    """Which concurrent write barrier discipline is in force."""

    SATB_DELETE = "satb_delete"            # shade overwritten (chosen default)
    INCREMENTAL_UPDATE = "incremental_update"  # shade new target (comparison only)


class Epoch(enum.Enum):
    """Phases of one collection cycle. Linear, one direction only."""

    IDLE = "idle"
    ROOT_SNAPSHOT = "root_snapshot"   # roots captured; SATB snapshot taken
    CONCURRENT_MARK = "concurrent_mark"
    REMARK = "remark"                 # drain SATB buffer at a safe point
    SWEEP = "sweep"                   # reclaim WHITE, non-moving
    EVACUATE = "evacuate"             # selective nursery evacuation (safe point)


@dataclass
class Barrier:
    """Records the SATB write-barrier buffer for the current cycle.

    In SATB, a store ``page.slot = new`` (modelled as adding/replacing an entry
    in ``page.remembered``) must shade the **overwritten** target grey so it is
    not lost from the snapshot. Entries collected here are drained at REMARK.
    """

    kind: BarrierKind = BarrierKind.SATB_DELETE
    buffer: List[tuple] = field(default_factory=list)  # BlockId.key() of shaded

    def record_overwrite(self, old_target: tuple) -> None:
        self.buffer.append(old_target)

    def drain(self) -> List[tuple]:
        out = list(self.buffer)
        self.buffer.clear()
        return out


class CpuCollector:
    """A CPU-only partitioned concurrent collector state machine.

    Operates over a :class:`Substrate`. Colors are held here (not on the page)
    so the substrate stays a pure metadata layer. The collector's job in this
    slice is to demonstrate the **reachability-preservation** contract:

        after a full cycle, every page reachable from a root at the moment the
        snapshot was taken is still ALLOCATED (never swept), and everything
        unreachable-at-snapshot with no post-snapshot revival is reclaimed.
    """

    def __init__(self, substrate: Substrate, *, barrier: BarrierKind = BarrierKind.SATB_DELETE) -> None:
        self.substrate = substrate
        self.epoch = Epoch.IDLE
        self.barrier = Barrier(kind=barrier)
        self._color: Dict[tuple, Color] = {}
        self._roots: Set[tuple] = set()
        self._snapshot_roots: Set[tuple] = set()
        self.cycle_count = 0

    # -- roots --------------------------------------------------------------

    def add_root(self, page: Page) -> None:
        self._roots.add(page.block_id.key())

    def remove_root(self, page: Page) -> None:
        self._roots.discard(page.block_id.key())

    def roots(self) -> Set[tuple]:
        return set(self._roots)

    def color_of(self, key: tuple) -> Color:
        return self._color.get(key, Color.WHITE)

    # -- mutator, under the barrier ----------------------------------------

    def mutator_store(self, page: Page, new_target: Optional[tuple]) -> None:
        """Model a pointer store into ``page``'s single tracked slot.

        Under SATB, before we overwrite we shade the previously-referenced
        target grey (the "deletion barrier"). Under INCREMENTAL_UPDATE we would
        shade ``new_target`` instead — provided only for comparison.
        """
        # Determine the currently-referenced target (the one being overwritten).
        # We model the "slot" as the *last* entry added to remembered; for a
        # cleaner model we just shade every current remembered target that is
        # about to be dropped.
        if self.epoch in (Epoch.CONCURRENT_MARK, Epoch.ROOT_SNAPSHOT):
            if self.barrier.kind is BarrierKind.SATB_DELETE:
                for old in page.remembered:
                    if self.color_of(old) is Color.WHITE:
                        self.barrier.record_overwrite(old)
            elif self.barrier.kind is BarrierKind.INCREMENTAL_UPDATE:
                if new_target is not None and self.color_of(new_target) is Color.WHITE:
                    self.barrier.record_overwrite(new_target)
        # Apply the store to the model.
        page.remembered.clear()
        if new_target is not None:
            page.remembered.add(new_target)

    # -- cycle phases -------------------------------------------------------

    def begin_cycle(self) -> None:
        if self.epoch is not Epoch.IDLE:
            raise CollectorError(f"cannot begin cycle from {self.epoch}")
        self.epoch = Epoch.ROOT_SNAPSHOT
        self._color = {}
        self.barrier.buffer.clear()
        # SATB: capture the root set snapshot. Everything reachable from these
        # roots at this instant is guaranteed to survive this cycle.
        self._snapshot_roots = set(self._roots)
        for key in self._snapshot_roots:
            self._color[key] = Color.GREY

    def concurrent_mark(self) -> None:
        """Drain the grey worklist by following remembered sets. Concurrent
        with the mutator (which shades via the SATB barrier)."""
        if self.epoch is not Epoch.ROOT_SNAPSHOT:
            raise CollectorError(f"cannot mark from {self.epoch}")
        self.epoch = Epoch.CONCURRENT_MARK
        self._drain_worklist()

    def remark(self) -> None:
        """Drain the SATB barrier buffer at a safe point, then finish marking.

        This is what makes SATB sound: anything the mutator overwrote during
        concurrent marking was shaded grey and is re-scanned here, so no live
        object is lost from the snapshot.
        """
        if self.epoch is not Epoch.CONCURRENT_MARK:
            raise CollectorError(f"cannot remark from {self.epoch}")
        self.epoch = Epoch.REMARK
        for key in self.barrier.drain():
            # A shaded key that still exists becomes grey and is re-scanned.
            if self._page_exists(key) and self.color_of(key) is not Color.BLACK:
                self._color[key] = Color.GREY
        self._drain_worklist()

    def sweep(self) -> List[tuple]:
        """Reclaim WHITE pages. Non-moving: OLD/PINNED are freed in place.

        Returns the list of reclaimed page keys.
        """
        if self.epoch is not Epoch.REMARK:
            raise CollectorError(f"cannot sweep from {self.epoch}")
        self.epoch = Epoch.SWEEP
        reclaimed: List[tuple] = []
        for page in list(self.substrate.pages()):
            key = page.block_id.key()
            if page.state != PageState.ALLOCATED:
                continue
            if self.color_of(key) is Color.WHITE:
                # Unreachable at snapshot and not revived — reclaim.
                # Drop all refs the collector implicitly holds.
                while page.refcount > 1:
                    self.substrate.free(page)
                self.substrate.free(page)
                reclaimed.append(key)
        return reclaimed

    def evacuate_nursery(self, region: Region) -> List[tuple]:
        """Selective evacuation of live nursery pages at a safe epoch.

        Only NURSERY regions are eligible (mostly-non-moving guarantee). Live
        pages are *modelled* as copied to fresh pages in an OLD region and the
        source metadata retired. Returns keys of evacuated source pages.

        This does not move any real object. It does copy the page metadata that
        represents live contents, remaps roots and remembered-set references to
        destination keys, then retires source metadata. If an OLD region is full,
        the model allocates another OLD region instead of dropping live state.
        """
        if self.epoch is not Epoch.SWEEP:
            raise CollectorError("evacuation only at the post-sweep safe point")
        if region.kind is not RegionKind.NURSERY:
            raise CollectorError("only nursery regions may be evacuated")
        moving = [
            page for page in list(region.pages.values())
            if page.state is PageState.ALLOCATED and page.movable
        ]
        remap: Dict[tuple, tuple] = {}
        destinations: Dict[tuple, Page] = {}
        for page in moving:
            source_key = page.block_id.key()
            old_region = self._old_region_with_free_slot(region.capacity)
            dest = self.substrate.allocate(
                old_region,
                page.layout,
                content_hash=page.content_hash,
                epoch=page.last_touch_epoch,
            )
            dest.refcount = max(1, page.refcount)
            dest.live_slots = set(page.live_slots)
            remap[source_key] = dest.block_id.key()
            destinations[source_key] = dest

        for source_key, dest in destinations.items():
            source = self.substrate.page(source_key)
            dest.remembered = {remap.get(key, key) for key in source.remembered}

        if remap:
            self._roots = {remap.get(key, key) for key in self._roots}
            self._snapshot_roots = {
                remap.get(key, key) for key in self._snapshot_roots
            }
            self.barrier.buffer = [
                remap.get(key, key) for key in self.barrier.buffer
            ]
            for page in self.substrate.pages():
                page.remembered = {remap.get(key, key) for key in page.remembered}
            for source_key, dest_key in remap.items():
                color = self._color.pop(source_key, Color.WHITE)
                if color is not Color.WHITE:
                    self._color[dest_key] = color

        evacuated: List[tuple] = []
        for page in moving:
            self.substrate.begin_evacuate(page)
            self.substrate.complete_evacuate(page)
            evacuated.append(page.block_id.key())
        return evacuated

    def end_cycle(self) -> None:
        if self.epoch not in (Epoch.SWEEP,):
            raise CollectorError(f"cannot end cycle from {self.epoch}")
        self.epoch = Epoch.IDLE
        self.cycle_count += 1
        self._snapshot_roots = set()

    # -- convenience: run a full non-evacuating cycle ----------------------

    def run_cycle(self) -> List[tuple]:
        """Run snapshot -> mark -> remark -> sweep -> end and return reclaimed."""
        self.begin_cycle()
        self.concurrent_mark()
        self.remark()
        reclaimed = self.sweep()
        self.end_cycle()
        return reclaimed

    # -- reachability oracle (for tests) -----------------------------------

    def reachable_from_snapshot(self) -> Set[tuple]:
        """The transitive closure of ``_snapshot_roots`` over remembered sets.

        This is the *truth* the marking phase must reproduce. Tests compare the
        BLACK set against this to prove no live page was swept.
        """
        return self._closure(self._snapshot_roots)

    # -- internals ----------------------------------------------------------

    def _closure(self, roots: Set[tuple]) -> Set[tuple]:
        seen: Set[tuple] = set()
        work = list(roots)
        while work:
            key = work.pop()
            if key in seen or not self._page_exists(key):
                continue
            seen.add(key)
            page = self.substrate.page(key)
            work.extend(t for t in page.remembered if t not in seen)
        return seen

    def _drain_worklist(self) -> None:
        # Process every GREY page to BLACK, greying its remembered targets.
        progress = True
        while progress:
            progress = False
            for key, color in list(self._color.items()):
                if color is not Color.GREY:
                    continue
                if not self._page_exists(key):
                    self._color[key] = Color.BLACK
                    continue
                page = self.substrate.page(key)
                for tgt in page.remembered:
                    if self.color_of(tgt) is Color.WHITE and self._page_exists(tgt):
                        self._color[tgt] = Color.GREY
                        progress = True
                self._color[key] = Color.BLACK

    def _page_exists(self, key: tuple) -> bool:
        try:
            self.substrate.page(key)
            return True
        except Exception:
            return False

    def _old_region_with_free_slot(self, fallback_capacity: int) -> Region:
        for candidate in self.substrate.regions():
            if candidate.kind is not RegionKind.OLD:
                continue
            if next(candidate.free_indices(), None) is not None:
                return candidate
        return self.substrate.add_region(RegionKind.OLD, max(1, fallback_capacity))
