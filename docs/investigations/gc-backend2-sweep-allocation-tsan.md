# Investigation: Backend #2 sweep/allocation TSan race

## Status
resolved

## Problem Description
Backend #2 now has green correctness and CMS worker TSan stress gates, but the
production checklist still needs an explicit proof that tracing sweep cannot
race with mutator allocation/free. The first bounded collector-vs-mutator TSan
probe reports a race on `pcc_gc_cycle_requested`: one mutator allocation writes
the cycle request flag while another mutator's assist path reads it in
`pcc_gc_step()`.

## Repro
Focused TSan gate:

```bash
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_concurrent_collection.py::test_cms_collect_threadsanitizer_sweep_allocation_or_skip' -q -n0
```

Expected after the fix: the test passes or skips only when a TSan-capable clang
runtime is unavailable.

## Test [CONFIRMED]
Observed before the fix with the focused TSan gate:

```text
WARNING: ThreadSanitizer: data race
Read of size 1 ... pcc_gc_step py_gc_backend.c:1591
Previous write of size 1 ... pcc_gc_note_object_allocated_sized py_gc_backend.c
Location is global 'pcc_gc_cycle_requested'
```

An earlier unbounded version of the same probe also timed out inside explicit
collection while mutators kept allocating, so the final regression keeps the
mutator workload finite and uses TSan's race report as the failure signal.

## Proposals
- No.1 Keep tracing sweep under STW during explicit collect     [CONFIRMED]

## No.1 Keep tracing sweep under STW during explicit collect
### Code Change
If the TSan repro confirms a race, keep the explicit tracing sweep under a
stop-the-world window or otherwise serialize `pcc_gc_objects` traversal with
mutator allocation/free. Mirror any required behavior in the pcc-Python runtime
port.

The fix also makes `pcc_gc_mark_active` and `pcc_gc_cycle_requested` atomic,
because the first confirmed race was on the cycle-request flag through mutator
assist. `pcc_gc_has_sweep_candidate()` now reads `pcc_gc_objects` under the GC
graph lock, and tracing graph-lock loops no longer park at safepoints while
holding that lock. The test harness waits for the collector thread to finish
while the main thread keeps hitting safepoints before `join()`, so STW requests
cannot deadlock on a non-safepointing joiner.

### CONFIRMED
The focused TSan gate now passes:

```text
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_concurrent_collection.py::test_cms_collect_threadsanitizer_sweep_allocation_or_skip' -q -n0 -rxX
1 passed in 2.96s
```

The complete TSan concurrency file and Backend #2 gates also pass:

```text
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_concurrent_collection.py -q -n0 -rxX
3 passed in 7.67s

env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_backend_concurrent.py tests/test_gc_abstraction_surface.py tests/test_gc_threading_substrate.py -q -n0 -rxX
32 passed in 46.50s

env -u LC_ALL PCC_GC_BACKEND=2 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
201 passed in 173.14s

env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
201 passed in 172.92s
```

Both C runtime and pcc-Python runtime archive rebuilds passed.

## Report (only when the investigation is closing)
No.1 landed. Backend #2 no longer has an unproven collector-vs-mutator safety
hole in the current implementation: explicit tracing sweep is serialized by
STW, phase flags are atomic, and the object-list sweep candidate query is
locked. This is a conservative production-safety step, not a claim that Backend
#2 already matches Go's concurrent span sweeping or per-worker work-buffer
design. Those algorithmic/performance upgrades remain separate production-level
work.
