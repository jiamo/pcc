"""G-P3-LONGRUN workload 2: sawtooth live-set (grow N, drop half).

Exercises fragmentation and return-to-OS behavior: the live set grows
to GROW_TO objects, then half are dropped, repeatedly. Same CSV line
as longrun_churn:

    elapsed_ms,rss_bytes,peak_rss_bytes,pause_n,pause_sum_us,pause_max_us,ops

argv[1] = total sawtooth cycles (smoke passes a small number).
Deterministic; runs under any PCC_GC_BACKEND=0..4.
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

GROW_TO = 4096
SAMPLE_EVERY = 4


class Blob:
    def __init__(self, k: int):
        self.k = k
        self.payload = ["x" + str(k % 31), k, k * 2]


def main() -> int:
    cycles = 40
    if len(sys.argv) > 1:
        cycles = int(sys.argv[1])

    live = []
    start_us = pcc_monotonic_us()
    ops = 0
    c = 0
    while c < cycles:
        # grow to GROW_TO
        while len(live) < GROW_TO:
            live.append(Blob(ops))
            ops = ops + 1
        # drop half (keep evens by rebuilding — deterministic)
        kept = []
        i = 0
        while i < len(live):
            if i % 2 == 0:
                kept.append(live[i])
            i = i + 1
        live = kept
        if c % SAMPLE_EVERY == 0:
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
        c = c + 1
    print("done," + str(ops))
    return 0


main()
