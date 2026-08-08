# Investigation: GC3 `gc.collect()` undercounts a 10k two-node cycle workload (test_gc_collect_cycle_throughput red under PCC_GC_BACKEND=3)

## Status
active — pre-existing GC3 behavior gap surfaced (and attribution-proven NOT
caused) by the 2026-08-07 index-engine backward-shift slice. Not yet
root-caused.

## Problem Description
`tests/python/test_gc_performance.py::test_gc_collect_cycle_throughput`
builds 10,000 unreachable 2-node cycles and asserts `gc.collect() >= 20_000`.
Under `PCC_GC_BACKEND=3` the program prints `False` (collector returns fewer
than 20k); under backend 0 it passes. `tests/python/test_gc_effectiveness.py::
test_steady_state_cycle_workload_rss_plateaus` fails under GC3 in the same
gate run and is likely the RSS-visible face of the same undercount.

## Repro
```bash
# program: the test body verbatim (make_cycles(10_000); print(gc.collect() >= 20_000))
env -u LC_ALL uv run python -c "from pcc.py_frontend.pipeline import \
  compile_python; compile_python('cyc.py','cyc.out',ir_scaffold_mode='on')"
PCC_GC_BACKEND=3 ./cyc.out    # prints False (expected True), rc 0
```

## Test [CONFIRMED]
Observed 2026-08-07 under the pytest gate (`PCC_GC_BACKEND=3 ... -n0
tests/python/test_gc_*.py`: 3 failed / 538 passed) and via the standalone
repro. Control: identical `False` with the HEAD (pre-backward-shift) index
engine after a control rebuild of `libpy_runtime_pcc_py.a` → pre-existing.

## Proposals
- No.1 Determine whether the generational collector fails to TRACE the
  cycles (minor/major promotion never scans them) or fails to COUNT them
  (collected but the return-count telemetry misses); compare `gc.collect()`
  return vs a `__del__` canary count on the same workload.   [pending]
- No.2 If tracing: check the remembered-set / promotion paths against the
  2026-08 GC kernel rework commits (6a2e0475, c079c05a) — the strict-owner
  test family for generational scheduler/oldification/remembered-owners is
  ALSO red at HEAD (stale source-contract assertions), suggesting that
  rework landed partially unverified.                        [pending]

## Notes
- Sibling symptom: [gc4-trashcan-del-chain-dealloc-recursion-overflow.md](gc4-trashcan-del-chain-dealloc-recursion-overflow.md).
- Found during the index-engine optimization gating; see
  gc-frame-index-entry-pool-perf.md Update (2026-08-07, session 3) for the
  attribution controls.

## Update 2026-08-12: source root cause and implementation

Current source identifies a simpler first cause than the remembered-set
hypothesis: the public explicit tracing collection called the shared sweep with
a budget of 1024.  Marking found the 10k two-node candidate graph, but PASS 1
cleared only the first 1024 candidates and PASS 2 could therefore reclaim and
report only that batch.  The remainder stayed candidate/live for a later call,
which also explains the steady-state RSS symptom.

`pcc_gc_collect_tracing` now treats `gc.collect()` as the documented full-heap
boundary: under one stop-the-world transaction it invokes the three-pass sweep
with signed `INT64_MAX` (`9223372036854775807` in the freestanding pcc-Python
owner).  The retained C oracle mirrors the same call, and focused source tests
reject the old 1024 budget.  This is an implementation-only update; GC3
throughput, RSS, finalizer and bootstrap gates have not run, so runtime closure
is not yet CONFIRMED.
