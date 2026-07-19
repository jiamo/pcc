"""Five-GC advantage workload matrix.

This benchmark is intentionally small and deterministic.  It is not a global
collector ranking; each named workload stresses a different runtime profile so
that `benchmarks/run_gc_advantage_matrix.py` can measure where a backend has a
real advantage and where that advantage is only relative.

Output is newline-separated `key,value` pairs so the host runner can parse the
same compiled binary under `PCC_GC_BACKEND=0..4`.
"""
import gc
import sys
from typing import Any

from pcc.extern import c_int64, c_ptr, c_void, extern


pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)
pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)
pcc_gc_object_id = extern("pcc_gc_object_id", (c_ptr,), c_int64)
pcc_os_peak_rss_bytes = extern("pcc_os_peak_rss_bytes", (), c_int64)
pcc_os_heap_in_use_bytes = extern("pcc_os_heap_in_use_bytes", (), c_int64)
pcc_os_heap_capacity_bytes = extern("pcc_os_heap_capacity_bytes", (), c_int64)
pcc_runtime_monotonic_us = extern("pcc_runtime_monotonic_us", (), c_int64)
pcc_gc_backend4_evacuated_bytes = extern(
    "pcc_gc_backend4_evacuated_bytes", (), c_int64
)
pcc_gc_backend4_zpage_count = extern("pcc_gc_backend4_zpage_count", (), c_int64)
pcc_gc_backend4_zpage_capacity_bytes = extern(
    "pcc_gc_backend4_zpage_capacity_bytes", (), c_int64
)
pcc_gc_backend4_zpage_used_bytes = extern(
    "pcc_gc_backend4_zpage_used_bytes", (), c_int64
)
pcc_gc_backend4_zpage_allocated_bytes = extern(
    "pcc_gc_backend4_zpage_allocated_bytes", (), c_int64
)
pcc_gc_backend4_zpage_reclaimable_gap_bytes = extern(
    "pcc_gc_backend4_zpage_reclaimable_gap_bytes", (), c_int64
)
pcc_gc_backend4_zpage_span_bytes = extern(
    "pcc_gc_backend4_zpage_span_bytes", (), c_int64
)
pcc_gc_backend4_zpage_free_pages = extern(
    "pcc_gc_backend4_zpage_free_pages", (), c_int64
)
pcc_gc_backend4_zpage_free_capacity_bytes = extern(
    "pcc_gc_backend4_zpage_free_capacity_bytes", (), c_int64
)
pcc_gc_backend4_zpage_free_span_bytes = extern(
    "pcc_gc_backend4_zpage_free_span_bytes", (), c_int64
)


MAX_PAUSE_US = 7
PAUSE_COUNT = 32
PAUSE_SUM_US = 33
RELOCATION_FORWARDS = 15
RELOCATION_BARRIER_FORWARDS = 16
WORK_STEPS = 5


class Node:
    def __init__(self, k: int):
        self.k = k
        self.a = None
        self.b = None
        self.items = [k, k + 1, k + 2]


class Tiny:
    def __init__(self, k: int):
        self.k = k
        self.next = None


def mark_id(x: Any) -> int:
    return pcc_gc_object_id(x)


def list_churn(n: int, rounds: int, inner: int, collect_every: int) -> int:
    ring = []
    i = 0
    while i < n:
        ring.append([i, i + 1, i + 2])
        i = i + 1

    ops = 0
    r = 0
    while r < rounds:
        j = 0
        while j < 64:
            idx = (r * 64 + j) % n
            x = []
            k = 0
            while k < inner:
                x.append(r + j + k)
                k = k + 1
            ring[idx] = x
            ops = ops + len(x)
            j = j + 1
        if collect_every > 0 and r % collect_every == 0:
            gc.collect()
        r = r + 1
    return ops + len(ring)


def node_churn(n: int, rounds: int, inner: int, collect_every: int) -> int:
    ring = []
    i = 0
    while i < n:
        ring.append(Node(i))
        i = i + 1

    ops = 0
    r = 0
    while r < rounds:
        j = 0
        while j < 64:
            idx = (r * 64 + j) % n
            node = Node(r * 64 + j)
            if inner > 3:
                node.items.append(inner)
            ring[idx] = node
            ops = ops + node.k
            j = j + 1
        if collect_every > 0 and r % collect_every == 0:
            gc.collect()
        r = r + 1
    return ops + len(ring)


def keep_lists(n: int, rounds: int, inner: int, collect_every: int) -> int:
    roots = []
    i = 0
    while i < n:
        x = []
        k = 0
        while k < inner:
            x.append(i + k)
            k = k + 1
        roots.append(x)
        i = i + 1

    total = 0
    r = 0
    while r < rounds:
        i = 0
        while i < n:
            total = total + len(roots[i])
            i = i + 31
        if collect_every > 0 and r % collect_every == 0:
            gc.collect()
        r = r + 1
    return total


def pointer_mutation(n: int, rounds: int, inner: int, collect_every: int) -> int:
    nodes = []
    i = 0
    while i < n:
        nodes.append(Tiny(i))
        i = i + 1

    ops = 0
    r = 0
    while r < rounds:
        stride = (r % 31) + 1
        j = 0
        while j < 128:
            a = nodes[(r * 128 + j) % n]
            b = nodes[(r * 128 + j * stride + 7) % n]
            a.next = b
            b.next = a
            ops = ops + 1
            j = j + 1
        if collect_every > 0 and r % collect_every == 0:
            gc.collect()
        r = r + 1
    return ops + nodes[0].k


def step_relocating_lists(n: int, rounds: int, inner: int, collect_every: int) -> int:
    roots = []
    i = 0
    while i < n:
        x = [i, i + 1, i + 2, i + 3]
        roots.append(x)
        mark_id(x)
        i = i + 1

    moved = 0
    r = 0
    while r < rounds:
        moved = moved + pcc_gc_step(256)
        k = r % n
        roots[k].append(r)
        if len(roots[k]) > inner:
            roots[k].pop(0)
        r = r + 1
    return moved + len(roots)


def main() -> int:
    mode = sys.argv[1]
    n = int(sys.argv[2])
    rounds = int(sys.argv[3])
    inner = int(sys.argv[4])
    collect_every = int(sys.argv[5])

    pcc_gc_telemetry_reset()
    start_us = pcc_runtime_monotonic_us()
    result = 0
    if mode == "list_churn":
        result = list_churn(n, rounds, inner, collect_every)
    elif mode == "node_churn":
        result = node_churn(n, rounds, inner, collect_every)
    elif mode == "keep_lists":
        result = keep_lists(n, rounds, inner, collect_every)
    elif mode == "pointer_mutation":
        result = pointer_mutation(n, rounds, inner, collect_every)
    elif mode == "step_relocating_lists":
        result = step_relocating_lists(n, rounds, inner, collect_every)
    else:
        print("error,bad-mode")
        return 2

    elapsed_us = pcc_runtime_monotonic_us() - start_us
    print("result," + str(result))
    print("backend," + str(pcc_gc_backend()))
    print("elapsed_us," + str(elapsed_us))
    print("pause_count," + str(pcc_gc_telemetry(PAUSE_COUNT)))
    print("pause_sum_us," + str(pcc_gc_telemetry(PAUSE_SUM_US)))
    print("max_pause_us," + str(pcc_gc_telemetry(MAX_PAUSE_US)))
    print("reloc_forwards," + str(pcc_gc_telemetry(RELOCATION_FORWARDS)))
    print("reloc_barriers," + str(pcc_gc_telemetry(RELOCATION_BARRIER_FORWARDS)))
    print("work_steps," + str(pcc_gc_telemetry(WORK_STEPS)))
    print("evacuated_bytes," + str(pcc_gc_backend4_evacuated_bytes()))
    print("rss_bytes," + str(pcc_os_peak_rss_bytes()))
    print("heap_bytes," + str(pcc_os_heap_in_use_bytes()))
    print("heap_capacity_bytes," + str(pcc_os_heap_capacity_bytes()))
    print("zpage_count," + str(pcc_gc_backend4_zpage_count()))
    print("zpage_capacity_bytes," + str(pcc_gc_backend4_zpage_capacity_bytes()))
    print("zpage_used_bytes," + str(pcc_gc_backend4_zpage_used_bytes()))
    print("zpage_allocated_bytes," + str(pcc_gc_backend4_zpage_allocated_bytes()))
    print(
        "zpage_reclaimable_gap_bytes,"
        + str(pcc_gc_backend4_zpage_reclaimable_gap_bytes())
    )
    print("zpage_span_bytes," + str(pcc_gc_backend4_zpage_span_bytes()))
    print("zpage_free_pages," + str(pcc_gc_backend4_zpage_free_pages()))
    print(
        "zpage_free_capacity_bytes,"
        + str(pcc_gc_backend4_zpage_free_capacity_bytes())
    )
    print("zpage_free_span_bytes," + str(pcc_gc_backend4_zpage_free_span_bytes()))
    return 0


main()
