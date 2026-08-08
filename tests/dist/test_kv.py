"""Oracle tests for pcc.dist.kv (D-P0-DIST-KV-BRIDGE).

Local metadata-only BlockManager: deterministic prefix-hash, refcount, pin/
unpin, LRU/longest-prefix eviction, serialization round-trip, invalidation.
"""
import pytest

from pcc.dist import kv
from pcc.dist.kv import BlockManager, KVBlockHandle, KVError


# --- deterministic prefix hashing / sharing --------------------------------
def test_deterministic_hash_and_prefix_sharing():
    m = BlockManager(block_tokens=2)
    a = m.allocate([1, 2, 3, 4, 5, 6])
    b = m.allocate([1, 2, 3, 4, 9, 9])  # shares first 2 blocks (prefix [1,2],[3,4])
    assert a[0].block_hash == b[0].block_hash
    assert a[1].block_hash == b[1].block_hash
    assert a[2].block_hash != b[2].block_hash  # divergent suffix
    # shared blocks got refcount bumped by both allocations
    assert m.refcount(a[0]) == 2
    assert m.refcount(a[2]) == 1  # only first sequence uses this suffix block


def test_hash_is_stable_across_managers():
    m1 = BlockManager(block_tokens=2)
    m2 = BlockManager(block_tokens=2)
    assert m1.allocate([7, 8, 9, 10])[0].block_hash == m2.allocate([7, 8, 9, 10])[0].block_hash


def test_different_block_size_differs():
    assert BlockManager(block_tokens=2).allocate([1, 2])[0].block_hash != \
        BlockManager(block_tokens=1).allocate([1, 2])[0].block_hash


# --- refcount --------------------------------------------------------------
def test_refcount_release_and_underflow():
    m = BlockManager(block_tokens=2)
    h = m.allocate([1, 2])
    assert m.refcount(h[0]) == 1
    m.release(h[0])
    assert m.refcount(h[0]) == 0
    with pytest.raises(KVError):
        m.release(h[0])  # underflow


def test_release_unknown_block():
    m = BlockManager(block_tokens=2)
    with pytest.raises(KVError):
        m.release(KVBlockHandle("deadbeef", 0, 2))


# --- pin / unpin -----------------------------------------------------------
def test_pin_blocks_eviction():
    m = BlockManager(block_tokens=1, capacity=3)
    seq = m.allocate([1, 2, 3])
    for h in seq:
        m.release(h)
    m.pin(seq[0])  # pin the shallowest (root prefix) block
    m.allocate([9])  # forces one eviction; pinned block must survive
    assert m.has_block(seq[0].block_hash)
    assert m.is_pinned(seq[0])


def test_unpin_underflow():
    m = BlockManager(block_tokens=1)
    h = m.allocate([1])
    with pytest.raises(KVError):
        m.unpin(h[0])


def test_invalidate_pinned_raises():
    m = BlockManager(block_tokens=1)
    h = m.allocate([1])
    m.pin(h[0])
    with pytest.raises(KVError):
        m.invalidate(h[0])


# --- eviction policy -------------------------------------------------------
def test_eviction_prefers_shortest_prefix_then_lru():
    # capacity forces eviction; longest-prefix retention => shallowest evicted.
    m = BlockManager(block_tokens=1, capacity=10)
    seq = m.allocate([1, 2, 3])  # depths 0,1,2
    for h in seq:
        m.release(h)
    victim = m.evict_one()
    # Only the leaf (depth 2) has no live children -> it is the sole candidate.
    assert victim == seq[2].block_hash


def test_eviction_returns_none_when_nothing_evictable():
    m = BlockManager(block_tokens=1)
    m.allocate([1])  # refcount 1, not released -> not evictable
    assert m.evict_one() is None


def test_capacity_full_with_no_victim_raises():
    m = BlockManager(block_tokens=1, capacity=1)
    m.allocate([1])  # holds the only slot with refcount 1
    with pytest.raises(KVError):
        m.allocate([2])  # cannot evict the in-use block


# --- serialization ---------------------------------------------------------
def test_handle_serialize_roundtrip():
    h = KVBlockHandle("abc123", 2, 4)
    assert KVBlockHandle.deserialize(h.serialize()) == h


def test_handle_deserialize_rejects_garbage():
    with pytest.raises(KVError):
        KVBlockHandle.deserialize("nope")
    with pytest.raises(KVError):
        KVBlockHandle.deserialize("kvblk:onlyone")


def test_manager_serialize_roundtrip():
    m = BlockManager(block_tokens=2, capacity=16)
    m.allocate([1, 2, 3, 4, 5, 6])
    m.allocate([1, 2, 99, 99])
    blob = m.serialize()
    back = BlockManager.deserialize(blob)
    assert back.num_blocks() == m.num_blocks()
    assert back.block_tokens == 2 and back.capacity == 16
    # deterministic re-serialization
    assert back.serialize() == blob


# --- invalidation ----------------------------------------------------------
def test_invalidate_drops_descendants():
    m = BlockManager(block_tokens=1)
    seq = m.allocate([1, 2, 3])  # chain depth 0->1->2
    removed = m.invalidate(seq[0])  # dropping root drops the whole chain
    assert set(removed) == {h.block_hash for h in seq}
    assert m.num_blocks() == 0


def test_invalidate_leaf_only():
    m = BlockManager(block_tokens=1)
    seq = m.allocate([1, 2, 3])
    removed = m.invalidate(seq[2])  # leaf only
    assert removed == [seq[2].block_hash]
    assert m.has_block(seq[0].block_hash) and m.has_block(seq[1].block_hash)


def test_bad_block_size_and_capacity():
    with pytest.raises(KVError):
        BlockManager(block_tokens=0)
    with pytest.raises(KVError):
        BlockManager(block_tokens=2, capacity=0)
    with pytest.raises(KVError):
        BlockManager(block_tokens=2).allocate([])
