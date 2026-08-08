"""substrate.py — blockized-heap metadata substrate (CPU-owned control plane).

This is the *foundations* slice from the GPU-GC roadmap: region/page
descriptors with **stable block identity**, touch/free/evict metadata, and
layout-class routing. It is a metadata-only model. Nothing here allocates real
memory, copies objects, or talks to a device. It exists so the collector's
correctness invariants can be tested on the CPU.

Vocabulary borrowed deliberately from vLLM's KV-block manager and from pcc's
runtime object model:

* **Stable block identity** (``BlockId``): a page's identity never changes for
  the lifetime of the substrate, even across free/reuse. This mirrors vLLM's
  "stable block identity, touch on reuse, tail insertion on free, metadata
  invalidation at eviction" and is the property a GPU kernel needs so it can
  hold a device-side handle across a collection without a pointer moving under
  it.
* **Layout class** (``LayoutClass``): routes a page to a tracing strategy. This
  is what the ``assist`` oracle keys its GPU/CPU decision on.

CLAIM BOUNDARY: CPU-only, non-moving *model*. No GPU kernels, no real
collector integration, no device memory.
"""
from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional


class SubstrateError(RuntimeError):
    """Raised on an invalid metadata transition (a modelled invariant break)."""


# ---------------------------------------------------------------------------
# Stable identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True, order=True)
class BlockId:
    """Stable, immutable identity of a page within a substrate.

    Frozen on purpose: identity must survive free/reuse and eviction. Two pages
    are the same block iff their ``BlockId`` compares equal. ``region`` and
    ``index`` fully determine it; ``serial`` is a monotonically-increasing
    allocation serial that lets telemetry distinguish reuse generations without
    changing identity.
    """

    region: int
    index: int
    serial: int = 0

    def key(self) -> "tuple[int, int]":
        """Identity key ignoring the reuse serial (region, index)."""
        return (self.region, self.index)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class RegionKind(enum.Enum):
    """Region role in the mostly-non-moving generational model.

    ``NURSERY`` regions are short-lived and are the only regions eligible for
    selective evacuation at a safe epoch. ``OLD`` regions are non-moving by
    default (the design's core "don't surprise a running kernel with a moving
    pointer" rule). ``PINNED`` regions are never moved and never evacuated
    (device-hot or otherwise externally referenced).
    """

    NURSERY = "nursery"
    OLD = "old"
    PINNED = "pinned"


class LayoutClass(enum.Enum):
    """Physical layout of the objects a page holds.

    The classifier in ``assist`` routes GPU vs CPU tracing off this. Regular,
    homogeneous layouts are GPU-friendly; pointer-rich polymorphic graphs are
    not (older GPU-GC work showed irregularity + atomics erase the parallel
    win).
    """

    FLAT_ARRAY = "flat_array"          # homogeneous, contiguous — GPU-ideal
    OBJECT_VECTOR = "object_vector"    # array of same-shape objects
    POINTER_TABLE = "pointer_table"    # dense table of pointers — GPU-summary
    IMMUTABLE = "immutable"            # frozen; content-hashable / reusable
    POINTER_GRAPH = "pointer_graph"    # irregular polymorphic — CPU-only
    RAW_PAYLOAD = "raw_payload"        # pointer-free bytes — trivially traceable


class PageState(enum.Enum):
    """Lifecycle state of a page. This is the substrate-level state machine.

    Legal transitions (enforced by :class:`Substrate`):

        FREE      --allocate-->  ALLOCATED
        ALLOCATED --touch------> ALLOCATED   (refcount++)
        ALLOCATED --free-------> FREE         (only when refcount hits 0)
        ALLOCATED --evacuate---> EVACUATING   (nursery only, at a safe epoch)
        EVACUATING--complete---> FREE
        ALLOCATED --evict------> EVICTED       (cold reclaim; identity retained)
        EVICTED   --allocate---> ALLOCATED    (reuse; new serial)

    Identity (``BlockId.key()``) is invariant across every transition.
    """

    FREE = "free"
    ALLOCATED = "allocated"
    EVACUATING = "evacuating"
    EVICTED = "evicted"


# ---------------------------------------------------------------------------
# Page descriptor
# ---------------------------------------------------------------------------

@dataclass
class Page:
    """Compact page descriptor — the unit of GPU/CPU residency and tracing.

    Fields mirror the roadmap's "compact descriptor": region/page id, layout
    class, pinned/movable bit, liveness bitmap handle, remembered-set summary,
    last-touch epoch, residency class, and an optional content hash for
    reusable immutable pages.
    """

    block_id: BlockId
    layout: LayoutClass
    state: PageState = PageState.FREE
    movable: bool = True
    refcount: int = 0
    # Modelled liveness bitmap: a set of live object slot indices. In a real
    # collector this is a device/host bitmap pointer; here it is the truth the
    # GPU oracle must reproduce.
    live_slots: set = field(default_factory=set)
    # Remembered-set / card summary: outgoing references to *other* pages, by
    # BlockId key. This is what makes reachability testable without real memory.
    remembered: set = field(default_factory=set)
    last_touch_epoch: int = 0
    content_hash: Optional[str] = None

    @property
    def region(self) -> int:
        return self.block_id.region

    @property
    def pinned(self) -> bool:
        return not self.movable

    def is_reusable_immutable(self) -> bool:
        return self.layout is LayoutClass.IMMUTABLE and self.content_hash is not None


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------

@dataclass
class Region:
    """A fixed-size region subdivided into pages. CPU-owned metadata only."""

    region_id: int
    kind: RegionKind
    capacity: int
    pages: Dict[int, Page] = field(default_factory=dict)

    @property
    def movable(self) -> bool:
        # OLD is non-moving by default; PINNED never moves; only NURSERY moves.
        return self.kind is RegionKind.NURSERY

    def free_indices(self) -> Iterator[int]:
        for i in range(self.capacity):
            pg = self.pages.get(i)
            if pg is None or pg.state in (PageState.FREE, PageState.EVICTED):
                yield i


# ---------------------------------------------------------------------------
# Substrate — the control plane
# ---------------------------------------------------------------------------

class Substrate:
    """CPU-owned control plane over regions and pages.

    Enforces the :class:`PageState` transition rules and preserves stable block
    identity. A free queue is modelled per the vLLM discipline: freed blocks go
    to the **tail** so low-reuse blocks are evicted first, and a page reaching
    the head is the eviction candidate.
    """

    def __init__(self) -> None:
        self._regions: Dict[int, Region] = {}
        self._serial = itertools.count(1)
        # Free/reuse queue of BlockId.key() in tail-insertion order.
        self._free_queue: List[tuple] = []
        self._region_counter = itertools.count()

    # -- region management --------------------------------------------------

    def add_region(self, kind: RegionKind, capacity: int) -> Region:
        if capacity <= 0:
            raise SubstrateError("region capacity must be positive")
        rid = next(self._region_counter)
        region = Region(region_id=rid, kind=kind, capacity=capacity)
        self._regions[rid] = region
        return region

    def region(self, rid: int) -> Region:
        try:
            return self._regions[rid]
        except KeyError:
            raise SubstrateError(f"no such region {rid}") from None

    def regions(self) -> Iterator[Region]:
        return iter(self._regions.values())

    def page(self, key: tuple) -> Page:
        region = self.region(key[0])
        pg = region.pages.get(key[1])
        if pg is None:
            raise SubstrateError(f"no such page {key}")
        return pg

    def pages(self) -> Iterator[Page]:
        for region in self._regions.values():
            yield from region.pages.values()

    # -- lifecycle ----------------------------------------------------------

    def allocate(
        self,
        region: Region,
        layout: LayoutClass,
        *,
        index: Optional[int] = None,
        content_hash: Optional[str] = None,
        epoch: int = 0,
    ) -> Page:
        """Allocate (or reuse) a page in ``region``. Identity is stable."""
        if region.region_id not in self._regions:
            raise SubstrateError("region not owned by this substrate")
        if index is None:
            index = next(region.free_indices(), None)
            if index is None:
                raise SubstrateError(f"region {region.region_id} is full")
        existing = region.pages.get(index)
        if existing is not None and existing.state == PageState.ALLOCATED:
            raise SubstrateError(f"page {(region.region_id, index)} already allocated")

        serial = next(self._serial)
        # PINNED regions produce non-movable pages; others movable.
        movable = region.movable and layout is not LayoutClass.IMMUTABLE
        bid = BlockId(region=region.region_id, index=index, serial=serial)
        page = Page(
            block_id=bid,
            layout=layout,
            state=PageState.ALLOCATED,
            movable=movable,
            refcount=1,
            content_hash=content_hash,
            last_touch_epoch=epoch,
        )
        region.pages[index] = page
        # Reuse removes it from the free queue if present.
        self._free_queue = [k for k in self._free_queue if k != bid.key()]
        return page

    def touch(self, page: Page, *, epoch: int = 0) -> None:
        """Reuse-touch: increment refcount and refresh last-touch epoch.

        Mirrors vLLM prefix-cache "touch on reuse". A touched page is removed
        from the free queue since it is now live again.
        """
        if page.state not in (PageState.ALLOCATED, PageState.EVICTED):
            raise SubstrateError(f"cannot touch page in state {page.state}")
        if page.state == PageState.EVICTED:
            # Reviving an evicted-but-still-identified block.
            page.state = PageState.ALLOCATED
        page.refcount += 1
        page.last_touch_epoch = epoch
        self._free_queue = [k for k in self._free_queue if k != page.block_id.key()]

    def free(self, page: Page) -> None:
        """Drop one reference; when it reaches 0 the page becomes FREE.

        Freed identities go to the **tail** of the free queue (vLLM discipline:
        low-reuse blocks are evicted first).
        """
        if page.state not in (PageState.ALLOCATED, PageState.EVACUATING):
            raise SubstrateError(f"cannot free page in state {page.state}")
        if page.refcount <= 0:
            raise SubstrateError("refcount underflow on free")
        page.refcount -= 1
        if page.refcount == 0:
            page.state = PageState.FREE
            page.live_slots.clear()
            page.remembered.clear()
            key = page.block_id.key()
            if key not in self._free_queue:
                self._free_queue.append(key)

    def begin_evacuate(self, page: Page) -> None:
        """Mark a movable nursery page for selective evacuation at a safe epoch.

        Only NURSERY (movable) pages may evacuate; OLD/PINNED never move. This
        is the core "mostly non-moving" guard.
        """
        region = self.region(page.region)
        if not region.movable or not page.movable:
            raise SubstrateError("only movable nursery pages may evacuate")
        if page.state != PageState.ALLOCATED:
            raise SubstrateError(f"cannot evacuate page in state {page.state}")
        page.state = PageState.EVACUATING

    def complete_evacuate(self, page: Page) -> None:
        """Finish an evacuation: the source identity is retired to FREE.

        The *contents* are modelled as already copied to a fresh page by the
        collector; here we only retire the source metadata. Identity of the
        source is preserved as a key but the page is now free.
        """
        if page.state != PageState.EVACUATING:
            raise SubstrateError("page is not evacuating")
        page.state = PageState.FREE
        page.refcount = 0
        page.live_slots.clear()
        page.remembered.clear()
        key = page.block_id.key()
        if key not in self._free_queue:
            self._free_queue.append(key)

    def evict(self, page: Page) -> None:
        """Cold reclaim: metadata invalidated, identity retained for reuse.

        Distinct from ``free``: eviction is a spill/reclaim decision. The block
        keeps its identity key (so a directory can invalidate by key) but its
        liveness bitmap and remembered set are dropped.
        """
        if page.state not in (PageState.ALLOCATED, PageState.FREE):
            raise SubstrateError(f"cannot evict page in state {page.state}")
        page.state = PageState.EVICTED
        page.refcount = 0
        page.live_slots.clear()
        page.remembered.clear()
        page.content_hash = None

    # -- free-queue introspection ------------------------------------------

    def free_queue(self) -> List[tuple]:
        """Copy of the free/reuse queue in tail-insertion order (head first)."""
        return list(self._free_queue)

    def eviction_candidate(self) -> Optional[tuple]:
        """The head of the free queue is the next eviction candidate, if any."""
        return self._free_queue[0] if self._free_queue else None

    # -- invariant checks ---------------------------------------------------

    def check_invariants(self) -> None:
        """Assert the substrate-level invariants hold. Raises on violation.

        1. Every allocated page has refcount >= 1; every FREE has refcount 0.
        2. Stable identity: no two live pages share a ``BlockId.key()``.
        3. Remembered-set targets resolve to existing page keys.
        4. Non-movable regions contain no EVACUATING page.
        """
        seen_keys: set = set()
        for region in self._regions.values():
            for idx, pg in region.pages.items():
                if pg.block_id.key() != (region.region_id, idx):
                    raise SubstrateError(
                        f"identity drift: page at {(region.region_id, idx)} "
                        f"carries {pg.block_id.key()}"
                    )
                if pg.state == PageState.ALLOCATED and pg.refcount < 1:
                    raise SubstrateError(f"allocated page {pg.block_id.key()} has refcount 0")
                if pg.state == PageState.FREE and pg.refcount != 0:
                    raise SubstrateError(f"free page {pg.block_id.key()} has nonzero refcount")
                if pg.state == PageState.EVACUATING and not region.movable:
                    raise SubstrateError(
                        f"non-movable region {region.region_id} has evacuating page"
                    )
                if pg.state == PageState.ALLOCATED:
                    key = pg.block_id.key()
                    if key in seen_keys:
                        raise SubstrateError(f"duplicate live identity {key}")
                    seen_keys.add(key)
        # remembered-set closure
        all_keys = {(r.region_id, i) for r in self._regions.values() for i in r.pages}
        for pg in self.pages():
            for tgt in pg.remembered:
                if tgt not in all_keys:
                    raise SubstrateError(
                        f"remembered-set of {pg.block_id.key()} points at unknown {tgt}"
                    )
