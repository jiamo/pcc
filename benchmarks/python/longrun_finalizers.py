"""G-P3-LONGRUN workload 4: finalizer/weakref churn + leak canary.

Continuously creates objects with __del__ finalizers and weakrefs to
ordinary instances, dropping them each round. Doubles as a LEAK
CANARY: finalized count must track created count (within the live
window) — a growing gap means finalizers stopped running or objects
leak. CSV line adds the canary gap as the last field:

    elapsed_ms,rss_bytes,peak_rss_bytes,pause_n,pause_sum_us,pause_max_us,ops,heap_in_use,heap_capacity,canary_gap

argv[1] = rounds. Deterministic; runs under any PCC_GC_BACKEND=0..4.
"""
import sys
import weakref

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

SAMPLE_EVERY = 100
BATCH = 64

finalized = 0
created = 0


class Tracked:
    def __init__(self, k: int):
        self.k = k

    def __del__(self):
        global finalized
        finalized = finalized + 1


class Plain:
    def __init__(self, k: int):
        self.k = k


def main() -> int:
    global created
    rounds = 500
    if len(sys.argv) > 1:
        rounds = int(sys.argv[1])

    start_us = pcc_monotonic_us()
    ops = 0
    r = 0
    while r < rounds:
        batch = []
        i = 0
        while i < BATCH:
            t = Tracked(r * BATCH + i)
            created = created + 1
            p = Plain(i)
            wr = weakref.ref(p)
            if wr() is None:
                print("corrupt")
                return 1
            batch.append(t)
            ops = ops + 1
            i = i + 1
        batch = []
        if r % SAMPLE_EVERY == 0:
            elapsed = (pcc_monotonic_us() - start_us) // 1000
            gap = created - finalized
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
                + ","
                + str(gap)
            )
        r = r + 1
    final_gap = created - finalized
    print("done," + str(ops) + "," + str(final_gap))
    return 0


main()
