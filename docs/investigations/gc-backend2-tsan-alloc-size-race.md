# Investigation: Backend #2 TSan allocation-size race

## Status
resolved

## Problem Description
The first full-runtime ThreadSanitizer probe for Backend #2 CMS worker stress
reports races in `pcc_gc_note_alloc()`. The formal pytest gate first reports
`pcc_gc_metrics[PCC_GC_COUNTER_ALLOCATIONS]`; a temporary probe also reported
`pcc_gc_last_alloc_bytes`. Multiple mutator threads call allocation telemetry
concurrently. The metrics/debt counters need atomic access, and the shared size
scratch slot can assign the wrong size to a newly allocated object under
concurrent allocation.

## Repro
Focused TSan gate:

```bash
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_concurrent_collection.py::test_cms_worker_threadsanitizer_stress_or_skip' -q -n0
```

Expected after the fix: the test passes or skips only when a TSan-capable clang
runtime is unavailable.

## Test [CONFIRMED]
Observed before the fix with the formal TSan gate:

```text
WARNING: ThreadSanitizer: data race
Write of size 8 ... pcc_gc_note_alloc py_gc_backend.c:1569
Location is global 'pcc_gc_metrics'
```

Also observed with a temporary full-runtime TSan probe:

```text
WARNING: ThreadSanitizer: data race
Write of size 8 ... pcc_gc_note_alloc py_gc_backend.c:1568
Location is global 'pcc_gc_last_alloc_bytes'
```

## Proposals
- No.1 Remove allocation scratch size and make allocation telemetry atomic     [CONFIRMED]

## No.1 Remove allocation scratch size and make allocation telemetry atomic
### Code Change
Replace the shared `pcc_gc_last_alloc_bytes` scratch slot with an internal
`pcc_gc_note_object_allocated_sized(o, size)` path used by `pcc_gc_alloc()`.
Keep the old unsized registration entry as a compatibility wrapper that uses
`sizeof(PyObjectHeader)`. Mirror the sized path in the pcc-Python runtime port.
Use atomic operations for GC metrics and debt fields that are touched by
multiple mutators during Backend #2 stress.

The final patch also had to make the surrounding threaded GC surface
ThreadSanitizer-clean: header flag updates now use atomic helpers, the
refcount-cycle side table is protected by a small runtime lock, the CMS work
queue is serialized by a spin lock, per-thread allocation scratch state moved
to `_Thread_local`, and lazy one-time runtime-log/debug configuration caches
use atomic compare/exchange.

### CONFIRMED
The focused TSan gate now passes:

```text
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_concurrent_collection.py -q -n0 -rxX
2 passed in 5.06s
```

The broader Backend #2 and runtime mirror gates also pass:

```text
env -u LC_ALL PCC_GC_BACKEND=2 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
200 passed in 171.55s

env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_backend_concurrent.py tests/test_gc_abstraction_surface.py tests/test_gc_threading_substrate.py -q -n0 -rxX
32 passed in 23.10s

env -u LC_ALL /opt/homebrew/bin/timeout 360s make -B -C pcc/py_runtime PCC='uv run pcc' PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 libpy_runtime_pcc_py.a
passed
```

No ThreadSanitizer data-race reports remain in the CMS worker stress gate.

## Report (only when the investigation is closing)
No.1 landed. Removing `pcc_gc_last_alloc_bytes` closed the size attribution
race directly, and atomics around metrics/debt/flags closed the remaining
multi-mutator reports that appeared once the first race was gone. The CMS queue
and refcount-cycle side table locks are conservative, but they match the current
production priority: make Backend #2 correct and TSan-clean before optimizing
the queue protocol back toward Go-style per-worker buffers.
