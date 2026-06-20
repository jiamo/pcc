"""CPU-only oracle tests for pcc.gpu_gc.substrate.

Encodes the substrate invariants: stable block identity across free/reuse and
eviction, the page-state machine's legal/illegal transitions, refcount
correctness, remembered-set closure, and the vLLM-style tail-insertion free
queue. No GPU, no real memory.
"""
from __future__ import annotations

import pytest

from pcc.gpu_gc.substrate import (
    BlockId,
    LayoutClass,
    Page,
    PageState,
    RegionKind,
    Substrate,
    SubstrateError,
)


def _fresh():
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 4)
    nur = sub.add_region(RegionKind.NURSERY, 4)
    return sub, old, nur


def test_block_id_is_stable_identity():
    a = BlockId(region=1, index=2, serial=5)
    b = BlockId(region=1, index=2, serial=9)
    # Reuse serial differs but identity key is the same.
    assert a.key() == b.key() == (1, 2)
    # Frozen: cannot mutate identity.
    with pytest.raises(Exception):
        a.region = 3  # type: ignore[misc]


def test_allocate_assigns_stable_key_and_serial_increments():
    sub, old, _ = _fresh()
    p0 = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    p1 = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    assert p0.block_id.key() == (old.region_id, 0)
    assert p1.block_id.key() == (old.region_id, 1)
    assert p1.block_id.serial > p0.block_id.serial
    sub.check_invariants()


def test_reuse_after_free_keeps_identity_key_new_serial():
    sub, old, _ = _fresh()
    p = sub.allocate(old, LayoutClass.FLAT_ARRAY, index=0)
    old_serial = p.block_id.serial
    sub.free(p)
    assert p.state is PageState.FREE
    p2 = sub.allocate(old, LayoutClass.FLAT_ARRAY, index=0)
    assert p2.block_id.key() == (old.region_id, 0)
    assert p2.block_id.serial != old_serial
    sub.check_invariants()


def test_touch_increments_and_free_decrements_refcount():
    sub, old, _ = _fresh()
    p = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    assert p.refcount == 1
    sub.touch(p)
    assert p.refcount == 2
    sub.free(p)
    assert p.state is PageState.ALLOCATED and p.refcount == 1
    sub.free(p)
    assert p.state is PageState.FREE and p.refcount == 0


def test_free_underflow_rejected():
    sub, old, _ = _fresh()
    p = sub.allocate(old, LayoutClass.FLAT_ARRAY)
    sub.free(p)
    with pytest.raises(SubstrateError):
        sub.free(p)


def test_free_queue_tail_insertion_and_eviction_head():
    sub, old, _ = _fresh()
    a = sub.allocate(old, LayoutClass.FLAT_ARRAY, index=0)
    b = sub.allocate(old, LayoutClass.FLAT_ARRAY, index=1)
    sub.free(a)  # freed first -> head (evicted first)
    sub.free(b)
    assert sub.free_queue() == [a.block_id.key(), b.block_id.key()]
    assert sub.eviction_candidate() == a.block_id.key()


def test_allocate_removes_from_free_queue_on_reuse():
    sub, old, _ = _fresh()
    a = sub.allocate(old, LayoutClass.FLAT_ARRAY, index=0)
    sub.free(a)
    assert a.block_id.key() in sub.free_queue()
    sub.allocate(old, LayoutClass.FLAT_ARRAY, index=0)
    assert a.block_id.key() not in sub.free_queue()


def test_evacuation_only_for_movable_nursery():
    sub, old, nur = _fresh()
    op = sub.allocate(old, LayoutClass.FLAT_ARRAY)   # non-movable region
    np_ = sub.allocate(nur, LayoutClass.OBJECT_VECTOR)  # movable region
    with pytest.raises(SubstrateError):
        sub.begin_evacuate(op)
    sub.begin_evacuate(np_)
    assert np_.state is PageState.EVACUATING
    sub.complete_evacuate(np_)
    assert np_.state is PageState.FREE


def test_pinned_region_pages_never_movable():
    sub = Substrate()
    pin = sub.add_region(RegionKind.PINNED, 2)
    p = sub.allocate(pin, LayoutClass.RAW_PAYLOAD)
    assert p.pinned is True
    with pytest.raises(SubstrateError):
        sub.begin_evacuate(p)


def test_evict_retains_identity_but_drops_metadata():
    sub, old, _ = _fresh()
    p = sub.allocate(old, LayoutClass.IMMUTABLE, content_hash="deadbeef")
    p.live_slots.update({1, 2, 3})
    key = p.block_id.key()
    sub.evict(p)
    assert p.state is PageState.EVICTED
    assert p.block_id.key() == key      # identity retained
    assert p.live_slots == set()        # metadata dropped
    assert p.content_hash is None
    # An evicted block can be revived via touch.
    sub.touch(p)
    assert p.state is PageState.ALLOCATED


def test_remembered_set_closure_invariant_catches_dangling_ref():
    sub, old, _ = _fresh()
    p = sub.allocate(old, LayoutClass.POINTER_GRAPH)
    p.remembered.add((old.region_id, 999))  # points at a nonexistent page
    with pytest.raises(SubstrateError):
        sub.check_invariants()


def test_immutable_layout_page_is_non_movable():
    sub, old, nur = _fresh()
    p = sub.allocate(nur, LayoutClass.IMMUTABLE)
    # Even in a nursery, immutable pages are not movable.
    assert p.movable is False


def test_region_full_rejects_allocation():
    sub = Substrate()
    r = sub.add_region(RegionKind.OLD, 1)
    sub.allocate(r, LayoutClass.FLAT_ARRAY)
    with pytest.raises(SubstrateError):
        sub.allocate(r, LayoutClass.FLAT_ARRAY)


def test_invariants_detect_identity_drift():
    sub, old, _ = _fresh()
    p = sub.allocate(old, LayoutClass.FLAT_ARRAY, index=0)
    # Force identity drift (simulating a bug) and confirm the checker catches it.
    p.block_id = BlockId(region=old.region_id, index=3, serial=p.block_id.serial)
    with pytest.raises(SubstrateError):
        sub.check_invariants()
