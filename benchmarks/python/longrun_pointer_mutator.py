"""G-P3-LONGRUN workload 3: heavy pointer mutation over a stable graph.

A fixed population of nodes continuously rewires its `next`/`buddy`
pointers (deterministic stride walk), generating write-barrier and
remembered-set pressure with minimal allocation. Same CSV contract as
longrun_churn. argv[1] = rounds. Runs under any PCC_GC_BACKEND=0..4.
"""
import sys

from pcc.extern import c_int64, extern

pcc_os_current_rss_bytes = extern("pcc_os_current_rss_bytes", (), c_int64)
pcc_os_peak_rss_bytes = extern("pcc_os_peak_rss_bytes", (), c_int64)
pcc_os_heap_in_use_bytes = extern("pcc_os_heap_in_use_bytes", (), c_int64)
pcc_os_heap_capacity_bytes = extern("pcc_os_heap_capacity_bytes", (), c_int64)
pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
pcc_monotonic_us = extern("pcc_runtime_monotonic_us", (), c_int64)

PAUSE_COUNT = 32
PAUSE_SUM_US = 33
MAX_PAUSE_US = 7

POPULATION = 4096
SAMPLE_EVERY = 200


class GraphNode:
    def __init__(self, k: int):
        self.k = k
        self.next = None
        self.buddy = None


def main() -> int:
    rounds = 1000
    if len(sys.argv) > 1:
        rounds = int(sys.argv[1])

    nodes = []
    i = 0
    while i < POPULATION:
        nodes.append(GraphNode(i))
        i = i + 1

    start_us = pcc_monotonic_us()
    ops = 0
    r = 0
    while r < rounds:
        stride = (r % 31) + 1
        idx = 0
        while idx < 128:
            a = nodes[(r * 128 + idx) % POPULATION]
            b = nodes[(r * 128 + idx * stride + 7) % POPULATION]
            a.next = b
            b.buddy = a
            ops = ops + 1
            idx = idx + 1
        # spot-check graph integrity
        probe = nodes[(r * 128) % POPULATION]
        if probe.next is not None and probe.next.buddy is not probe:
            # buddy may have been rewired by a later store in the same
            # round; only flag a corrupt OBJECT, not a stale edge
            if probe.next.k < 0 or probe.next.k >= POPULATION:
                print("corrupt")
                return 1
        if r % SAMPLE_EVERY == 0:
            elapsed = (pcc_monotonic_us() - start_us) // 1000
            print(
                str(elapsed)
                + ","
                + str(pcc_os_current_rss_bytes())
                + ","
                + str(pcc_os_peak_rss_bytes())
                + ","
                + str(pcc_gc_telemetry(PAUSE_COUNT))
                + ","
                + str(pcc_gc_telemetry(PAUSE_SUM_US))
                + ","
                + str(pcc_gc_telemetry(MAX_PAUSE_US))
                + ","
                + str(ops)
                + ","
                + str(pcc_os_heap_in_use_bytes())
                + ","
                + str(pcc_os_heap_capacity_bytes())
            )
        r = r + 1
    print("done," + str(ops))
    return 0


main()
