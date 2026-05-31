# Investigation: Backend 2 concurrent mark worker

## Status
resolved

## Problem Description
Implement the next Tier 5 GC backend productionization slice from `goal.md`:
Backend #2 is currently synchronous and shares the tracing core with backend #1.
It lacks a pthread-backed background mark worker, a mark/work queue, and
mutator assist under allocation pressure.  The first slice must be independent
from the backend #1 and backend #3 work already landed, and must not describe
backend #2 as production until a real concurrent marking gate is verified.

## Repro
Run the focused backend #2 gate:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_concurrent.py -q -n0
```

Expected current failure: the threaded runtime archive builds, but backend #2
does not expose worker-start, queue-push, worker-drain, or mutator-assist
telemetry.

## Test [CONFIRMED]
The focused gate has been observed failing:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_concurrent.py -q -n0
# 1 failed in 3.32s
```

Observed failure:

- `pcc_gc_telemetry(11)` returns `-1`, proving backend #2 does not yet expose
  worker-start telemetry.  The same missing surface also covers queue, drain,
  and mutator-assist counters.

## Proposals
- No.1 Add CMS worker ticket queue and mutator assist telemetry     [CONFIRMED]

## No.1 Add CMS worker ticket queue and mutator assist telemetry
### Code Change
The landed slice:

- add backend #2 telemetry counters for worker starts, queue pushes, worker
  drains, and mutator assists;
- lazily start one detached pthread worker when backend #2 is selected in a
  `PCC_WITH_THREADS=1` runtime;
- enqueue bounded allocation work tickets from backend #2 allocation pressure;
- have the background worker drain tickets without touching object graph state
  yet, avoiding races with the current unsynchronized tracked-object list;
- force bounded mutator assist when backend #2 allocation debt crosses the
  configured debt threshold;
- mirror the telemetry and assist accounting in the pcc-Python runtime port so
  the runtime archive rebuild gate stays meaningful.

Touched files:

- `pcc/py_runtime/src/py_gc_backend.c`
- `pcc/py_runtime/include/py_runtime.h`
- `pcc/py_runtime/py/py_gc_backend.py`
- `pcc/py_runtime/py/py_substrate.py`
- `tests/test_gc_backend_concurrent.py`
- `tests/test_gc_abstraction_surface.py`

### CONFIRMED
The focused threaded backend #2 gate now passes:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_concurrent.py -q -n0
# 1 passed in 2.99s
```

The new gate and public GC-counter surface pass together:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_concurrent.py tests/test_gc_abstraction_surface.py -q -n0
# 15 passed in 30.08s
```

Both runtime archives rebuild:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL \
  make -B -C pcc/py_runtime libpy_runtime.a
# success; existing unused-function warnings remain for tracing helpers

/opt/homebrew/bin/timeout 420s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" \
  make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
# success
```

Threading and existing backend-2 tracing compatibility checks pass:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_threading_substrate.py -q -n0
# 12 passed in 2.35s

/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  'tests/test_gc_effectiveness.py::test_non_default_backends_collect_list_cycle[2]' \
  'tests/test_gc_effectiveness.py::test_non_default_backends_collect_cross_type_cycle[2]' \
  -q -n0
# 2 passed in 1.50s

/opt/homebrew/bin/timeout 240s env -u LC_ALL PCC_GC_BACKEND=2 uv run pytest \
  tests/test_gc_backend_concurrent.py \
  tests/test_gc_backend_incremental.py \
  tests/test_gc_backend_generational.py \
  -q -n0
# 5 passed in 5.77s
```

## Report (only when the investigation is closing)
No.1 landed.  Backend #2 now has its first worker-backed productionization
surface: a `PCC_WITH_THREADS=1` runtime starts a detached GC worker for backend
#2, allocation pressure enqueues bounded CMS work tickets, the worker drains
those tickets at safepoints, and high allocation debt forces bounded mutator
assist through the existing tracing step path.

## Update: production hardening evidence

Backend #2 now has focused production gates beyond the initial worker-ticket
telemetry slice:

- worker traces gray barrier work;
- write-barrier buffers batch gray objects into the CMS queue;
- positive allocation work can be traced by the worker;
- mark termination can be reached by the worker without a mutator `gc_step`;
- worker lifecycle stops and restarts across backend switches;
- ThreadSanitizer stress covers concurrent worker drain and sweep/allocation
  interaction.

Confirmed focused gates:

```text
tests/python/test_gc_concurrent_collection.py::test_cms_worker_threadsanitizer_stress_or_skip
tests/python/test_gc_concurrent_collection.py::test_cms_collect_threadsanitizer_sweep_allocation_or_skip
# 2 passed in 9.64s

tests/python/test_gc_backend_concurrent.py
# 6 passed in 22.02s
```

The worker now traces object-graph state under the runtime's stop-the-world and
GC graph-lock boundary.  This is still not a literal Go work-buffer clone, but
the pcc production contract is no longer just "worker started": the gate covers
worker tracing, barrier work delivery, mark termination, lifecycle, and TSan
stress.
