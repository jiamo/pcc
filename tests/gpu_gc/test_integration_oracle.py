"""End-to-end CPU-only oracle: substrate + collector + assist + metal + tiered.

Proves the five modules compose into one coherent control plane and that the
top-level package re-exports work. This is the closest this slice gets to a
"realistic scenario", and it is deliberately still CPU-only metadata.
"""
from __future__ import annotations

import pcc.gpu_gc as g


def test_package_reexports_present():
    for name in (
        "Substrate", "RegionKind", "LayoutClass", "PageState", "BlockId",
        "CpuCollector", "BarrierKind", "Color", "Epoch",
        "AssistOracle", "AssistClass", "classify_page",
        "MetalResidencyAdapter", "ResidencyMode", "AdapterStatus",
        "BlockDirectory", "content_hash",
    ):
        assert hasattr(g, name), name


def test_full_lifecycle_preserves_reachability_and_reuse():
    sub = g.Substrate()
    old = sub.add_region(g.RegionKind.OLD, 8)
    nur = sub.add_region(g.RegionKind.NURSERY, 4)

    # Build a small heap: root -> arr (GPU-traceable), root -> graph (CPU-only),
    # plus a detached garbage page.
    root = sub.allocate(old, g.LayoutClass.POINTER_GRAPH)
    arr = sub.allocate(old, g.LayoutClass.FLAT_ARRAY)
    arr.live_slots.update({0, 1, 2})
    graph = sub.allocate(old, g.LayoutClass.POINTER_GRAPH)
    graph.live_slots.update({0})
    garbage = sub.allocate(nur, g.LayoutClass.OBJECT_VECTOR)
    root.remembered.update({arr.block_id.key(), graph.block_id.key()})
    sub.check_invariants()

    # Residency: mark the GPU-traceable array GPU-hot, the graph CPU-hot.
    adapter = g.MetalResidencyAdapter()
    st_arr, _ = adapter.set_residency(arr, g.ResidencyMode.GPU_HOT)
    st_graph, _ = adapter.set_residency(graph, g.ResidencyMode.CPU_HOT)
    assert st_arr is g.AdapterStatus.SKIPPED_WITH_REASON  # no-op oracle
    assert adapter.residency_of(arr) is g.ResidencyMode.GPU_HOT

    # Assist: classify + oracle-verify marking. Good kernel on the array.
    oracle = g.AssistOracle()
    good = lambda pg: set(pg.live_slots)
    assert g.classify_page(arr) is g.AssistClass.GPU_TRACEABLE
    assert g.classify_page(graph) is g.AssistClass.CPU_ONLY
    assert oracle.assisted_mark(arr, good) == {0, 1, 2}
    assert oracle.assisted_mark(graph, good) == {0}       # CPU_ONLY, not dispatched
    assert oracle.telemetry.gpu_dispatched == 1

    # Collect: root snapshot keeps root/arr/graph; garbage reclaimed.
    col = g.CpuCollector(sub)
    col.add_root(root)
    reclaimed = col.run_cycle()
    assert garbage.block_id.key() in reclaimed
    assert root.state is g.PageState.ALLOCATED
    assert arr.state is g.PageState.ALLOCATED
    assert graph.state is g.PageState.ALLOCATED
    sub.check_invariants()

    # Tiered reuse: an immutable snapshot of the array's payload is
    # content-addressed; a re-request hits without recompute.
    directory = g.BlockDirectory()
    payload = bytes(sorted(arr.live_slots))
    e1 = directory.register(payload, payload="frozen-arr")
    recompute_calls = []
    e2 = directory.get_or_recompute(
        payload, lambda: recompute_calls.append(1) or "recomputed"
    )
    assert e1 is e2
    assert recompute_calls == []  # hit, no recompute

    # Invalidate -> next get recomputes (recompute-on-failure contract).
    directory.invalidate(g.content_hash(payload))
    e3 = directory.get_or_recompute(
        payload, lambda: recompute_calls.append(1) or "recomputed"
    )
    assert recompute_calls == [1]
    assert e3.payload == "recomputed"


def test_reachability_invariant_holds_over_repeated_cycles():
    """Stress-lite: many small cycles never lose a reachable page or corrupt
    identity."""
    sub = g.Substrate()
    old = sub.add_region(g.RegionKind.OLD, 64)
    # Persistent live spine.
    spine = [sub.allocate(old, g.LayoutClass.POINTER_GRAPH) for _ in range(4)]
    for a, b in zip(spine, spine[1:]):
        a.remembered.add(b.block_id.key())
    col = g.CpuCollector(sub)
    col.add_root(spine[0])

    total_reclaimed = 0
    for _ in range(10):
        # Allocate transient garbage each round.
        garb = [sub.allocate(old, g.LayoutClass.FLAT_ARRAY) for _ in range(3)]
        reclaimed = col.run_cycle()
        total_reclaimed += len(reclaimed)
        # Spine always survives.
        for pg in spine:
            assert pg.state is g.PageState.ALLOCATED
        sub.check_invariants()
    assert total_reclaimed >= 20  # roughly 3 garbage pages * ~10 cycles
