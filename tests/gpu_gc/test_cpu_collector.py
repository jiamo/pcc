"""CPU-only oracle tests for pcc.gpu_gc.cpu_collector.

The central contract: the concurrent-marking state machine PRESERVES
REACHABILITY. Every page reachable from a root at snapshot time survives the
cycle; unreachable-at-snapshot pages with no revival are reclaimed. SATB
soundness is tested directly (a mutator that drops a reference mid-mark must not
cause a live-at-snapshot object to be swept).
"""
from __future__ import annotations

import pytest

from pcc.gpu_gc.cpu_collector import (
    BarrierKind,
    CollectorError,
    Color,
    CpuCollector,
    Epoch,
)
from pcc.gpu_gc.substrate import LayoutClass, PageState, RegionKind, Substrate


def _chain(sub, region, layouts):
    """Allocate pages and link them in a remembered-set chain p0->p1->..."""
    pages = [sub.allocate(region, lay) for lay in layouts]
    for a, b in zip(pages, pages[1:]):
        a.remembered.add(b.block_id.key())
    return pages


def test_epoch_sequence_is_linear_and_guarded():
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 4)
    col = CpuCollector(sub)
    assert col.epoch is Epoch.IDLE
    # Cannot mark before snapshot.
    with pytest.raises(CollectorError):
        col.concurrent_mark()
    col.begin_cycle()
    assert col.epoch is Epoch.ROOT_SNAPSHOT
    # Cannot begin twice.
    with pytest.raises(CollectorError):
        col.begin_cycle()


def test_reachable_pages_survive_unreachable_reclaimed():
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 8)
    root, live = _chain(sub, old, [LayoutClass.POINTER_GRAPH, LayoutClass.FLAT_ARRAY])
    garbage = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    col = CpuCollector(sub)
    col.add_root(root)
    reclaimed = col.run_cycle()
    assert garbage.block_id.key() in reclaimed
    assert root.state is PageState.ALLOCATED
    assert live.state is PageState.ALLOCATED
    assert garbage.state is PageState.FREE


def test_mark_result_equals_reachability_closure():
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 16)
    pages = _chain(sub, old, [LayoutClass.POINTER_GRAPH] * 5)
    # a side branch off the middle
    branch = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    pages[2].remembered.add(branch.block_id.key())
    col = CpuCollector(sub)
    col.add_root(pages[0])
    col.begin_cycle()
    col.concurrent_mark()
    col.remark()
    truth = col.reachable_from_snapshot()
    black = {k for k in truth if col.color_of(k) is Color.BLACK}
    # Everything reachable is BLACK (fully marked).
    assert black == truth
    assert branch.block_id.key() in truth
    col.sweep()
    col.end_cycle()


def test_satb_barrier_preserves_dropped_reference_this_cycle():
    """SATB soundness: dropping a->victim mid-mark must NOT reclaim victim."""
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 8)
    root, a, victim = _chain(
        sub, old,
        [LayoutClass.POINTER_GRAPH, LayoutClass.POINTER_GRAPH, LayoutClass.FLAT_ARRAY],
    )
    col = CpuCollector(sub, barrier=BarrierKind.SATB_DELETE)
    col.add_root(root)
    col.begin_cycle()
    # Mutator drops a->victim while marking is in flight.
    col.mutator_store(a, None)
    col.concurrent_mark()
    col.remark()
    reclaimed = col.sweep()
    col.end_cycle()
    # victim was reachable at the snapshot; SATB keeps it alive this cycle.
    assert victim.block_id.key() not in reclaimed
    assert victim.state is PageState.ALLOCATED


def test_satb_floating_garbage_reclaimed_next_cycle():
    """The dropped reference becomes reclaimable on the FOLLOWING cycle."""
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 8)
    root, a, victim = _chain(
        sub, old,
        [LayoutClass.POINTER_GRAPH, LayoutClass.POINTER_GRAPH, LayoutClass.FLAT_ARRAY],
    )
    col = CpuCollector(sub)
    col.add_root(root)
    col.begin_cycle()
    col.mutator_store(a, None)
    col.concurrent_mark(); col.remark(); col.sweep(); col.end_cycle()
    assert victim.state is PageState.ALLOCATED  # floated
    # Next cycle: no barrier shading, victim is now genuinely unreachable.
    reclaimed2 = col.run_cycle()
    assert victim.block_id.key() in reclaimed2
    assert victim.state is PageState.FREE


def test_newly_added_edge_target_survives_via_snapshot_root():
    """A page linked to a live root before snapshot is retained even if the
    edge is added and then the collector runs."""
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 8)
    root = sub.allocate(old, LayoutClass.POINTER_GRAPH)
    newp = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    col = CpuCollector(sub)
    col.add_root(root)
    # Link before snapshot.
    root.remembered.add(newp.block_id.key())
    reclaimed = col.run_cycle()
    assert newp.block_id.key() not in reclaimed
    assert newp.state is PageState.ALLOCATED


def test_evacuate_nursery_only_at_safe_point_and_nursery_only():
    sub = Substrate()
    nur = sub.add_region(RegionKind.NURSERY, 4)
    old = sub.add_region(RegionKind.OLD, 4)
    p = sub.allocate(nur, LayoutClass.OBJECT_VECTOR)
    op = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    col = CpuCollector(sub)
    col.add_root(p)
    col.add_root(op)
    # Evacuation before sweep safe-point is illegal.
    col.begin_cycle()
    with pytest.raises(CollectorError):
        col.evacuate_nursery(nur)
    col.concurrent_mark(); col.remark(); col.sweep()
    evac = col.evacuate_nursery(nur)
    assert p.block_id.key() in evac
    assert p.state is PageState.FREE
    new_root = next(iter(col.roots() - {op.block_id.key()}))
    moved = sub.page(new_root)
    assert moved.state is PageState.ALLOCATED
    assert moved.region == old.region_id
    # OLD region cannot be evacuated.
    with pytest.raises(CollectorError):
        col.evacuate_nursery(old)
    col.end_cycle()
    assert op.state is PageState.ALLOCATED


def test_reachability_preserved_across_evacuation_epoch():
    """Evacuation copies nursery live metadata and remaps roots to OLD pages."""
    sub = Substrate()
    nur = sub.add_region(RegionKind.NURSERY, 4)
    old = sub.add_region(RegionKind.OLD, 4)
    survivor_old = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    nursery_live = sub.allocate(nur, LayoutClass.OBJECT_VECTOR)
    nursery_live.live_slots.update({3, 7})
    col = CpuCollector(sub)
    col.add_root(survivor_old)
    col.add_root(nursery_live)
    col.begin_cycle(); col.concurrent_mark(); col.remark(); col.sweep()
    old_keys_before = {p.block_id.key() for p in old.pages.values()}
    evacuated = col.evacuate_nursery(nur)
    col.end_cycle()
    assert nursery_live.block_id.key() in evacuated
    moved_keys = {
        p.block_id.key()
        for p in old.pages.values()
        if p.block_id.key() not in old_keys_before
    }
    assert len(moved_keys) == 1
    moved = sub.page(next(iter(moved_keys)))
    assert moved.live_slots == {3, 7}
    assert nursery_live.block_id.key() not in col.roots()
    assert moved.block_id.key() in col.roots()
    assert survivor_old.state is PageState.ALLOCATED
    sub.check_invariants()


def test_evacuate_nursery_remaps_edges_between_moved_pages():
    sub = Substrate()
    nur = sub.add_region(RegionKind.NURSERY, 4)
    old = sub.add_region(RegionKind.OLD, 1)
    root = sub.allocate(nur, LayoutClass.POINTER_GRAPH)
    child = sub.allocate(nur, LayoutClass.FLAT_ARRAY)
    root.remembered.add(child.block_id.key())
    col = CpuCollector(sub)
    col.add_root(root)
    col.begin_cycle(); col.concurrent_mark(); col.remark(); col.sweep()
    col.evacuate_nursery(nur)
    col.end_cycle()

    moved_root_key = next(iter(col.roots()))
    moved_root = sub.page(moved_root_key)
    assert moved_root.region != nur.region_id
    assert len(moved_root.remembered) == 1
    moved_child = sub.page(next(iter(moved_root.remembered)))
    assert moved_child.state is PageState.ALLOCATED
    assert moved_child.region != nur.region_id
    assert root.state is PageState.FREE
    assert child.state is PageState.FREE
    sub.check_invariants()


def test_cycle_count_advances():
    sub = Substrate()
    sub.add_region(RegionKind.OLD, 2)
    col = CpuCollector(sub)
    assert col.cycle_count == 0
    col.run_cycle()
    col.run_cycle()
    assert col.cycle_count == 2


def test_incremental_update_barrier_available_for_comparison():
    """The INCREMENTAL_UPDATE barrier exists as a comparison mode; it shades the
    new target. Documented as non-default; verify it is selectable and marks."""
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 8)
    root = sub.allocate(old, LayoutClass.POINTER_GRAPH)
    target = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    col = CpuCollector(sub, barrier=BarrierKind.INCREMENTAL_UPDATE)
    col.add_root(root)
    col.begin_cycle()
    # Store a new edge root->target during marking; IU shades the new target.
    col.mutator_store(root, target.block_id.key())
    col.concurrent_mark(); col.remark()
    reclaimed = col.sweep(); col.end_cycle()
    assert target.block_id.key() not in reclaimed
