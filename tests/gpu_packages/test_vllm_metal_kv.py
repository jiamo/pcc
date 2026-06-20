"""B-P0-VLLM-METAL-KV first-slice gate: metadata-only, mode-labeled.

WHAT THIS GATE PROVES
  1. A mode-labeled package-surface probe for the current vLLM Apple-Silicon
     path (out-of-tree ``vllm-metal`` plugin + its ``mlx`` dependency): present
     -> a placeholder assertion runs; absent -> an asserted SKIPPED_WITH_REASON
     verdict. (No import of any GPU kernel; no serving.)
  2. A REAL, deterministic, CPU-only KV-block metadata surrogate: paged-attention
     block table with block-id, refcount, prefix-hash, pin/unpin and eviction.
     These invariants are asserted directly (no device, no tensors).

WHAT THIS GATE EXPLICITLY DOES NOT PROVE
  - No pcc-native vLLM / MLX / Metal execution.
  - No attention math, no KV tensor movement, no throughput/scaling/serving.
  - The ``--python-libpython=off`` CPython-extension rejection is exercised
    through the real generic linkage scanner; this gate does not build or run
    pcc1.

See docs/design/pcc-vllm-minimind-gates.md for the full taxonomy.
"""

from __future__ import annotations

import pytest

from tests.gpu_packages.gpu_gate_common import (
    KVBlockTable,
    KVBlockTableError,
    chunk_into_blocks,
    cpython_extension_rejection_report,
    prefix_block_hash,
    probe_packages,
    synthetic_token_stream,
    vllm_metal_surface,
)


# ==========================================================================
# 1. Mode-labeled package-surface probe (present -> assert / absent -> verdict)
# ==========================================================================


def test_vllm_metal_package_surface_taxonomy():
    """Probe the vLLM Apple-Silicon surface with a uniform, mode-labeled verdict.

    No ``if package == 'vllm'`` special-casing: probe_packages sweeps a
    data-driven name list uniformly.
    """
    present, reason = vllm_metal_surface()
    surface = probe_packages(["vllm_metal", "vllm", "mlx"])
    # The probe result must be a clean bool map regardless of environment.
    assert set(surface) == {"vllm_metal", "vllm", "mlx"}
    assert all(isinstance(v, bool) for v in surface.values())

    if not present:
        assert reason.startswith("SKIPPED_WITH_REASON:")
        assert "vllm-metal" in reason
        assert "missing=" in reason
        return
    # Present branch: we still make NO serving/throughput claim. We only assert
    # that the plugin module is importable as a surface.
    assert surface["vllm_metal"] is True


# ==========================================================================
# 2. CPU-only KV-block metadata surrogate (REAL, asserted)
# ==========================================================================


def test_kv_block_alloc_and_free_refcount():
    tbl = KVBlockTable(num_blocks=4, block_size=8)
    assert tbl.free_count() == 4

    bid = tbl.allocate(token_ids=(1, 2, 3), parent_hash=None)
    assert tbl.free_count() == 3
    blk = tbl.snapshot(bid)
    assert blk.refcount == 1
    assert blk.prefix_hash == prefix_block_hash(None, (1, 2, 3))

    # incref / free round-trip.
    assert tbl.incref(bid) == 2
    assert tbl.free(bid) == 1
    assert tbl.free(bid) == 0
    # Now refcount 0 -> block returns to the free queue tail.
    assert tbl.free_count() == 4


def test_kv_block_double_free_raises():
    tbl = KVBlockTable(num_blocks=2, block_size=4)
    bid = tbl.allocate(token_ids=(9,), parent_hash=None)
    assert tbl.free(bid) == 0
    with pytest.raises(KVBlockTableError):
        tbl.free(bid)  # double free must be rejected, not silently wrap


def test_kv_block_prefix_hash_reuse_touches_same_block():
    """Content-addressed reuse: same (parent, tokens) -> same block, +refcount."""
    tbl = KVBlockTable(num_blocks=4, block_size=8)
    a = tbl.allocate(token_ids=(1, 2, 3, 4), parent_hash="ROOT")
    free_after_first = tbl.free_count()
    b = tbl.allocate(token_ids=(1, 2, 3, 4), parent_hash="ROOT")
    assert a == b  # reused, not a fresh block
    assert tbl.free_count() == free_after_first  # no new block consumed
    assert tbl.snapshot(a).refcount == 2


def test_kv_block_prefix_chain_distinct_hashes():
    """Different parents / tokens must hash to different identities."""
    h_root = prefix_block_hash(None, (1, 2))
    h_child = prefix_block_hash(h_root, (3, 4))
    assert h_root != h_child
    # Determinism: recomputation is stable.
    assert prefix_block_hash(h_root, (3, 4)) == h_child


def test_kv_block_pin_prevents_eviction():
    tbl = KVBlockTable(num_blocks=2, block_size=4)
    bid = tbl.allocate(token_ids=(5, 6), parent_hash=None)
    tbl.pin(bid)
    # Drop the last ref: pinned block must NOT rejoin the free queue.
    assert tbl.free(bid) == 0
    assert bid not in tbl._free_ids  # pinned block stays resident
    assert tbl.evict_one() != bid  # cannot evict a pinned block
    # Unpin -> now rejoins the free queue.
    tbl.unpin(bid)
    assert bid in tbl._free_ids


def test_kv_block_unpin_non_pinned_raises():
    tbl = KVBlockTable(num_blocks=1, block_size=4)
    bid = tbl.allocate(token_ids=(1,), parent_hash=None)
    with pytest.raises(KVBlockTableError):
        tbl.unpin(bid)


def test_kv_block_eviction_invalidates_hash_index():
    tbl = KVBlockTable(num_blocks=2, block_size=4)
    bid = tbl.allocate(token_ids=(7, 8), parent_hash=None)
    phash = tbl.snapshot(bid).prefix_hash
    tbl.free(bid)  # refcount 0 -> evictable
    evicted = tbl.evict_one()
    assert evicted == bid
    # After eviction the content hash must be gone: a fresh allocate with the
    # same tokens must NOT report a reuse hit for the stale identity.
    assert phash not in tbl._hash_index


def test_kv_block_reuse_for_new_tokens_invalidates_old_hash_index():
    tbl = KVBlockTable(num_blocks=1, block_size=4)
    old = tbl.allocate(token_ids=(1, 2, 3, 4), parent_hash=None)
    old_hash = tbl.snapshot(old).prefix_hash
    assert tbl.free(old) == 0

    reused = tbl.allocate(token_ids=(9, 9, 9, 9), parent_hash=None)
    assert reused == old
    assert old_hash not in tbl._hash_index
    assert tbl.snapshot(reused).token_ids == (9, 9, 9, 9)

    with pytest.raises(KVBlockTableError, match="no free blocks"):
        tbl.allocate(token_ids=(1, 2, 3, 4), parent_hash=None)


def test_kv_block_oom_surrogate_raises_not_wraps():
    tbl = KVBlockTable(num_blocks=1, block_size=4)
    tbl.allocate(token_ids=(1,), parent_hash=None)
    with pytest.raises(KVBlockTableError):
        tbl.allocate(token_ids=(2,), parent_hash=None)  # no free block


def test_kv_block_cache_hit_rate_is_metadata_only():
    """A deterministic hit-rate over a lookup trace. NOT a serving throughput
    claim -- pure metadata over the hash index."""
    tbl = KVBlockTable(num_blocks=8, block_size=4)
    toks = synthetic_token_stream(num_tokens=16, vocab_size=100, seed=7)
    blocks = chunk_into_blocks(toks, block_size=4)
    parent = None
    for blk in blocks:
        tbl.allocate(token_ids=blk, parent_hash=parent)
        parent = prefix_block_hash(parent, blk)
    # Re-looking-up the same chain must be a full hit; a novel chain a miss.
    same_chain = []
    parent = None
    for blk in blocks:
        same_chain.append((parent, blk))
        parent = prefix_block_hash(parent, blk)
    assert tbl.cache_hit_rate(same_chain) == 1.0
    novel = [(None, (999, 998, 997, 996))]
    assert tbl.cache_hit_rate(novel) == 0.0


def test_kv_block_free_queue_tail_lru_order():
    """Freed blocks append to the tail; oldest freed is evicted first."""
    tbl = KVBlockTable(num_blocks=3, block_size=4)
    a = tbl.allocate(token_ids=(1,), parent_hash="A")
    b = tbl.allocate(token_ids=(2,), parent_hash="B")
    tbl.free(a)  # a freed first
    tbl.free(b)  # b freed second -> behind a in the queue
    first_evicted = tbl.evict_one()
    assert first_evicted == a  # LRU: a (freed earliest) goes first


# ==========================================================================
# 3. --python-libpython=off package boundary (real linkage scan; no pcc1 build)
# ==========================================================================


def test_libpython_off_rejects_cpython_extension_surface(tmp_path):
    report = cpython_extension_rejection_report(
        tmp_path, "vllm_metal/_C.cpython-313-darwin.so"
    )

    assert report["ok"] is False
    assert report["execution_mode"] == "pcc-native"
    assert report["native_package_claim"] is False
    assert report["uses_cpython_extension_abi"] is True
    assert {item["code"] for item in report["diagnostics"]} >= {
        "PCC-PKG-003",
        "PCC-PKG-004",
    }
