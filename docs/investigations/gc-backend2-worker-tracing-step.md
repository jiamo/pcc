# Investigation: Backend 2 worker traces gray work

## Status
resolved

## Problem Description
Continue the Tier 5 backend #2 implementation from `goal.md`.  The previous
slice started a pthread CMS worker and added allocation-ticket / mutator-assist
telemetry, but the background worker deliberately did not trace object graph
state.  The next smallest step is to prove the worker can consume existing gray
work created by the mutator/write barrier without claiming full Go-style
concurrent mark-sweep.

## Repro
Run the focused backend #2 worker-tracing gate:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_gray_barrier_work \
  -q -n0
```

Expected current failure: `pcc_gc_telemetry(18)` is invalid or remains zero,
showing the worker drains tickets but does not perform trace work.

## Test [CONFIRMED]
The focused gate has been observed failing:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_gray_barrier_work \
  -q -n0
# FAILED: AssertionError: assert '0' == '1'
```

The probe exits normally, but `wait_for_worker_trace()` prints `0`; the
background worker has not made `pcc_gc_telemetry(18)` positive.

## Proposals
- No.1 Trace already-gray work from the CMS worker     [CONFIRMED]

## No.1 Trace already-gray work from the CMS worker
### Code Change
Implemented the smallest safe slice:

- add backend #2 telemetry for worker-side traced gray objects;
- let the CMS queue carry both allocation tickets and write-barrier gray-object
  work items;
- have the worker trace only queued gray objects when a mutator/write barrier
  has already made the mark state active;
- avoid making the worker start or finish global tracing cycles in this slice,
  so it does not introduce hidden STW waits from arbitrary allocation tickets;
- mirror the counter surface in the pcc-Python runtime port.

The focused test wait loop was also narrowed to sleep only.  Calling
`pcc_gc_safepoint()` from that wait loop lets the mutator run `pcc_gc_step(1)`
and steal the gray object before the worker can prove it did the work.

### CONFIRMED
The proposal fixes the focused gate and preserves the existing backend tests:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_gray_barrier_work \
  -q -n0
# 1 passed in 3.02s

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_concurrent.py tests/test_gc_abstraction_surface.py \
  -q -n0
# 16 passed in 37.11s

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_threading_substrate.py tests/test_gc_backend_*.py \
  -q -n0
# 23 passed in 12.21s

/opt/homebrew/bin/timeout 180s env -u LC_ALL make -B -C pcc/py_runtime \
  libpy_runtime.a
# exit 0

/opt/homebrew/bin/timeout 300s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" \
  make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
# exit 0

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_effectiveness.py -q -n0
# 21 passed, 3 xfailed, 3 xpassed in 20.14s
```

Backend #2 is still not production Go-style CMS.  The worker now consumes
write-barrier gray work, but this slice still does not add full mark
termination, lazy sweep, or TSan-clean synchronization around all object graph
access.

## Report (only when the investigation is closing)
No.1 landed.  The worker now performs real trace work for gray objects queued
by the CMS write barrier and reports it through
`PCC_GC_COUNTER_CMS_WORKER_TRACES`.  The implementation deliberately avoids
having the background worker begin or finish global tracing cycles, because
that would broaden this proposal into STW/termination behavior and risk
deadlocks from arbitrary allocation tickets.

This is a backend #2 progress slice, not a production-status update.
