# Investigation: Backend 2 buffered write barrier

## Status
resolved

## Problem Description
Continue the Backend #2 production-algorithm work from `goal.md`.  Backend #2
already has a TSan-clean conservative threaded CMS implementation, but its
write barrier still pushes every gray ticket directly into the global CMS
queue.  Go's `mwbbuf.go` uses a per-worker write-barrier buffer and flushes it
to GC work queues in batches; pcc should start moving the barrier path in that
direction without weakening the current STW sweep safety proof.

## Repro
Run the focused buffered-barrier gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_backend_concurrent.py::test_concurrent_backend_batches_gray_barrier_flushes' -q -n0
```

Expected after the fix: the probe reports at least one
`PCC_GC_COUNTER_CMS_WB_FLUSHES` event after enough active-cycle stores to fill
the Backend #2 barrier buffer.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_backend_concurrent.py::test_concurrent_backend_batches_gray_barrier_flushes' -q -n0
```

Result: `FAILED`.  The probe prints `-1` for telemetry counter 23, proving
the current runtime has no public buffered write-barrier flush signal.

## Proposals
- No.1 Add a thread-local Backend #2 write-barrier buffer     [CONFIRMED]

## No.1 Add a thread-local Backend #2 write-barrier buffer
### Code Change
Add a small `_Thread_local` buffer for Backend #2 gray-barrier tickets in the C
runtime.  The barrier still marks the value gray under the existing graph lock,
but it records the queue ticket in thread-local storage and flushes tickets to
the CMS queue in batches outside the graph lock.  Expose a telemetry counter so
tests can assert that the buffered path is exercised.  Mirror the public
counter in the pcc-Python runtime substrate.

### CONFIRMED
The C runtime now keeps a small `_Thread_local` Backend #2 write-barrier
buffer.  Active-cycle stores still shade white values while holding the graph
lock, but the CMS gray tickets are batched and flushed to the worker queue
outside the graph lock.  The public telemetry surface now exposes
`PCC_GC_COUNTER_CMS_WB_FLUSHES`; the pcc-Python runtime substrate and mirror
export the same counter.

Focused buffered-barrier gate:

```text
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_backend_concurrent.py::test_concurrent_backend_batches_gray_barrier_flushes' -q -n0
1 passed in 3.08s
```

Backend #2 concurrent gate:

```text
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_backend_concurrent.py -q -n0
6 passed in 17.89s
```

Surface/threading gate:

```text
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_abstraction_surface.py tests/test_gc_threading_substrate.py -q -n0
27 passed in 31.09s
```

TSan gate:

```text
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_concurrent_collection.py -q -n0 -rxX
3 passed in 8.88s
```

Full Backend #2 GC gate:

```text
env -u LC_ALL PCC_GC_BACKEND=2 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
202 passed in 184.82s
```

Default GC gate:

```text
env -u LC_ALL /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
202 passed in 175.58s
```

C runtime and pcc-Python runtime archive rebuilds also passed; the first
pcc-Python rebuild attempt failed only because `PATH` did not include
`.venv/bin`, then passed with:

```text
env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" /opt/homebrew/bin/timeout 900s make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
```

## Report (only when the investigation is closing)
No.1 landed.  Backend #2 now has the first Go `mwbbuf.go`-inspired write
barrier buffering slice: gray tickets are accumulated per mutator thread and
flushed to the CMS queue in batches, with a telemetry gate proving the path is
exercised.  This reduces pressure on the global CMS queue lock and avoids
taking that lock while the graph lock is held.

This does not close `goal.md` No.7 by itself.  Backend #2 still uses a
conservative global object graph, queue locks, and STW tracing sweep.  The
remaining production-algorithm work is a fuller Go-style work-buffer/drain
model and an explicit decision on whether to implement concurrent span/object
sweep instead of the current safe STW sweep.
