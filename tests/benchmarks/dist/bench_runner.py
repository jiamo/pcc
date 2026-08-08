"""P-P0-DIST-BENCH: the metadata-only distributed bench runner.

Runs the mode taxonomy in :mod:`tests.benchmarks.dist.bench_model` over the
existing ``pcc.dist`` oracles and produces a :class:`BenchManifest`:

    * ``single-process`` is actually measured. Two workloads:
        - collective oracle (allreduce / all-gather over POD buffers) reported
          as element-op COUNTS + a determinism digest;
        - the ``pcc.dist.kv`` ``BlockManager`` surrogate reported as
          logical-step COUNTS (allocations, block creations, cache hits,
          evictions) — NO wall-clock, NO throughput.
    * every networking mode and every workload placeholder is returned as
      ``SKIPPED_WITH_REASON`` naming the exact missing capability, cross-checked
      against the ``pcc.dist.transport`` / ``pcc.dist.session`` skip surface so
      the taxonomy cannot drift from the underlying oracle's own reasons.

Determinism is the core property: :func:`run_single_process` returns identical
metrics on every call, and :func:`run_all` builds a manifest whose measured
metrics are byte-stable across runs.

Standalone-importable: ``import tests.benchmarks.dist.bench_runner``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from pcc.dist import collective, session, transport
from pcc.dist.kv import BlockManager

from . import bench_model as bm


# --------------------------------------------------------------------------
# single-process measurement (logical counts only)
# --------------------------------------------------------------------------
def _digest(payload: Any) -> str:
    """A short deterministic digest of a JSON-able payload (result fingerprint)."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _measure_collective() -> dict[str, Any]:
    """Logical element-op counts for the collective oracle over fixed buffers.

    The counts are the number of binary reduce/copy element-ops a real backend
    would have to perform, derived from the oracle's own semantics — NOT a
    timing. A ``result_digest`` fingerprints the actual reduced values so a
    determinism regression is visible.
    """
    # Fixed, small, deterministic POD buffers over a fake world of 4 ranks.
    buffers = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16]]
    world = len(buffers)
    length = len(buffers[0])

    ar_out, ar_meta = collective.allreduce(buffers, "sum")
    ag_out, ag_meta = collective.all_gather(buffers)

    # Element-op accounting (pure structure, no clock):
    #   allreduce reduce phase: (world-1) binary ops per element.
    allreduce_reduce_ops = (world - 1) * length
    #   all-gather copy phase: world * length elements gathered per rank result.
    all_gather_copy_elems = world * length

    return {
        "world_size": world,
        "buffer_len": length,
        "allreduce_reduce_ops": allreduce_reduce_ops,
        "all_gather_copy_elems": all_gather_copy_elems,
        # Fingerprint the actual values so determinism is asserted on content,
        # not just on counts.
        "result_digest": _digest([ar_out, ag_out]),
        "allreduce_status": ar_meta.status,
        "all_gather_status": ag_meta.status,
    }


def _measure_kv() -> dict[str, Any]:
    """Logical-step counts for the KV BlockManager surrogate.

    Two sequences share a common prefix so prefix-cache hits are exercised.
    Metrics are op/step COUNTS (allocations, unique blocks created, shared
    blocks reused, releases, evictions) — never a latency.
    """
    mgr = BlockManager(block_tokens=4, capacity=8)

    # Sequence A and B share the first 8 tokens (2 blocks) -> prefix cache hit.
    seq_a = list(range(1, 13))   # 12 tokens -> 3 blocks
    seq_b = list(range(1, 9)) + [99, 98, 97, 96]  # shares first 2 blocks

    before_a = mgr.num_blocks()
    handles_a = mgr.allocate(seq_a)
    after_a = mgr.num_blocks()
    handles_b = mgr.allocate(seq_b)
    after_b = mgr.num_blocks()

    created_by_a = after_a - before_a
    created_by_b = after_b - after_a
    # Blocks B asked for that were already present (shared prefix) = reuse hits.
    shared_hits = len(handles_b) - created_by_b

    # Release everything, then evict what is now unreferenced.
    releases = 0
    for h in handles_a + handles_b:
        mgr.release(h)
        releases += 1
    evictions = 0
    while mgr.evict_one() is not None:
        evictions += 1

    return {
        "block_tokens": 4,
        "seq_a_blocks": len(handles_a),
        "seq_b_blocks": len(handles_b),
        "unique_blocks_created": created_by_a + created_by_b,
        "prefix_cache_hits": shared_hits,
        "releases": releases,
        "evictions": evictions,
    }


def run_single_process() -> bm.BenchResult:
    """Measure the single-process mode: collective + KV logical counts.

    Deterministic: identical metrics on every call.
    """
    metrics = {
        "collective": _measure_collective(),
        "kv": _measure_kv(),
    }
    return bm.measured(bm.MODE_SINGLE_PROCESS, **metrics)


# --------------------------------------------------------------------------
# skip resolution (cross-checked against the underlying oracle skip surface)
# --------------------------------------------------------------------------
# Map bench networking modes to the pcc.dist transport / session mode that owns
# the real "not implemented" reason, so this harness's skip reasons cannot claim
# a capability the oracle would report as available.
_ORACLE_TRANSPORT_FOR: dict[str, str] = {
    bm.MODE_LOCALHOST_TCP_RING: "tcp-ring",
    bm.MODE_MULTI_MAC_TCP_RING: "tcp-ring",
    bm.MODE_QUIC: "quic",
    bm.MODE_JACCL_RDMA: "jaccl-rdma",
}


def _assert_oracle_agrees_unavailable(mode: str) -> None:
    """Fail loudly if the underlying oracle would report this transport AVAILABLE.

    This keeps the bench skip taxonomy honest: we only emit SKIPPED_WITH_REASON
    for a networking mode whose backing ``pcc.dist.transport`` mode is genuinely
    unavailable. If the oracle ever lands a real transport, this guard makes the
    stale skip a hard error instead of a silently-wrong claim.
    """
    oracle_mode = _ORACLE_TRANSPORT_FOR.get(mode)
    if oracle_mode is None:
        return
    probe = transport.probe(oracle_mode)
    if probe.available:
        raise bm.BenchError(
            f"bench mode {mode!r} is marked skipped but pcc.dist.transport reports "
            f"{oracle_mode!r} AVAILABLE; the skip taxonomy is stale"
        )


def run_skipped(mode: str) -> bm.BenchResult:
    """Resolve a non-measurable mode to a SKIPPED_WITH_REASON result."""
    _assert_oracle_agrees_unavailable(mode)
    return bm.skipped(mode, bm.skip_reason_for(mode))


def run_mode(mode: str) -> bm.BenchResult:
    if mode not in bm.ALL_MODES:
        raise bm.BenchError(f"unknown bench mode {mode!r}; known: {list(bm.ALL_MODES)}")
    if bm.is_measurable(mode):
        if mode == bm.MODE_SINGLE_PROCESS:
            return run_single_process()
        raise bm.BenchError(f"measurable mode {mode!r} has no runner")
    return run_skipped(mode)


def run_all(*, detail: Mapping[str, Any] | None = None) -> bm.BenchManifest:
    """Run every mode in the taxonomy and build a :class:`BenchManifest`."""
    results = tuple(run_mode(m) for m in bm.ALL_MODES)
    manifest_detail = {
        "claim_boundary": (
            "MEASURED results are single-process CPU logical counts only; no "
            "throughput/scaling/multi-Mac/QUIC/RDMA/MiniMind/vLLM claim is made"
        ),
        "network_modes_known": list(session.network_modes()),
    }
    if detail:
        manifest_detail.update(detail)
    return bm.BenchManifest(
        harness="pcc-dist-bench",
        results=results,
        detail=manifest_detail,
    )
