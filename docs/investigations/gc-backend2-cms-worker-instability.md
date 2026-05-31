# Investigation: Backend #2 CMS worker is unstable under focused and full GC gates

## Status
resolved

## Problem Description
Backend #2 currently has a pthread CMS worker and a bounded queue, but the
worker path is unstable. During GC API work, the full GC gate repeatedly failed
in:

- `tests/test_gc_backend_concurrent.py::test_concurrent_backend_starts_worker_and_assists_allocations`

The child `cms_probe.out` exited with `SIGSEGV`.

A focused rerun of `tests/test_gc_backend_concurrent.py` then exposed a related
worker progress failure:

- `tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_gray_barrier_work`

The probe exited normally, but `wait_for_worker_trace()` returned `0`.

Treat this as a Backend #2 worker/queue synchronization or lifecycle problem,
not as part of the unrelated `gc.freeze()` API slice.

## Repro
Run the full GC gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed failing signature:

```text
tests/test_gc_backend_concurrent.py::test_concurrent_backend_starts_worker_and_assists_allocations
assert -11 == 0
```

Also run the focused Backend #2 gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_backend_concurrent.py -q -n0 -rxX
```

Observed failing signature:

```text
tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_gray_barrier_work
AssertionError: assert '0' == '1'
```

## Test [CONFIRMED]
The full GC gate failure was observed twice after the freeze API slice, both
times in `test_concurrent_backend_starts_worker_and_assists_allocations` with
`cms_probe.out` returning `-11`.

The focused Backend #2 gate was observed failing with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_backend_concurrent.py -q -n0 -rxX
```

Observed result: `1 failed, 2 passed`; the failing test was
`test_concurrent_backend_worker_traces_gray_barrier_work`, where worker trace
telemetry remained zero.

## Proposals
- No.1 Protect CMS worker graph access and queue progress with a runtime lock     [CONFIRMED]

## No.1 Protect CMS worker graph access and queue progress with a runtime lock
### Code Change
Add a small runtime graph lock in `pcc/py_runtime/src/py_gc_backend.c`. The
lock protects object graph/list access shared by the mutator and the detached
Backend #2 worker:

- object registration in `pcc_gc_note_object_allocated`
- object removal in `pcc_gc_note_object_freeing`
- minor-arena object-node removal in `pcc_gc_free_object_memory`
- mutator write-barrier color changes and gray-ticket enqueue
- mutator trace steps
- worker-side gray-ticket tracing and positive allocation-work tracing

The worker uses the same lock before touching object graph state. The lock
wait path calls `pcc_thread_safepoint()` and sleeps briefly, so a worker that is
waiting for mutator-owned graph access can still cooperate with stop-the-world
handshakes. Worker-side positive work now uses a worker-specific bounded trace
helper that can begin and advance marking but deliberately leaves final mark
termination to the mutator `pcc_gc_step()` path; that avoids a detached worker
running the STW finish phase while the mutator is also trying to mutate object
graph state.

After acquiring the lock, the worker also confirms that Backend #2 is still the
selected backend. If the process switches away from CMS while the detached
worker is alive, stale queue tickets are discarded without touching the object
graph.

No queue algorithm change was made. In particular, the worker does not requeue
work on lock contention, preserving the current single-producer/single-consumer
queue assumption.

### CONFIRMED
The focused Backend #2 gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_backend_concurrent.py -q -n0 -rxX
```

Observed result: `3 passed`.

The broader backend/threading gate and both C runtime archive builds pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_threading_substrate.py tests/test_gc_backend_*.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 180s make -B -C pcc/py_runtime libpy_runtime.a
env -u LC_ALL /opt/homebrew/bin/timeout 180s make -B -C pcc/py_runtime PCC_WITH_THREADS=1 libpy_runtime.a
```

Observed results: `31 passed`; both archive builds exited successfully.

The full GC gate now passes without the previous CMS SIGSEGV:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `168 passed, 17 xfailed`.

## Report (only when the investigation is closing)
No.1 landed. The root issue was that the detached CMS worker could traverse or
color the shared object graph while the mutator was concurrently inserting and
removing `PccGcObjectNode` entries during allocation/deallocation. Serializing
those graph accesses removes both observed failure modes: the full-suite
`cms_probe.out` SIGSEGV and the focused worker-trace telemetry miss.

This remains a productionization step, not full Go-style CMS. Backend #2 still
lacks mark termination, shutdown lifecycle, concurrent sweep proof, and
race-sanitized queue/marker validation.

## Update 2026-05-14: threaded CMS worker path restored under STW tracing

The threaded runtime had regressed into a contradictory state: the backend2
tests expected a CMS worker under `PCC_WITH_THREADS=1`, but the runtime
suppressed worker start, allocation queueing, CMS write-barrier flushing, and
worker tracing whenever threads were enabled. Re-enabling those paths exposed
the real safety boundary:

- The worker must not trace object internals concurrently with mutators.
  `pcc_gc_graph_lock` protects the GC object registry, not list/dict fields
  that mutators can resize or rewrite.
- The worker must not hold the graph lock and then request STW. A mutator can
  otherwise park while trying to acquire the graph lock with transient C
  arguments not yet stored into a GC root slot.

The current threaded CMS worker therefore handles every work item by stopping
the world first, then taking the graph lock, then tracing or finishing the mark
cycle. This keeps the worker/lifecycle/queue semantics active while preserving
the pcc1 real-thread explicit-`gc.collect()` hard gate.

Two related marker fixes landed with this:

- Root marking now force-grays roots, even if a fresh allocation had been
  colored black for construction-window protection. Ordinary child marking
  still skips black objects.
- CMS write barriers conservatively re-gray any non-gray value during an
  active CMS cycle, so fresh-black values stored during a cycle still reach the
  worker queue.

The mark-termination test was adjusted to respect fresh-allocation protection:
the first worker ticket ages fresh objects, and the second proves the worker
can reach sweep debt without a mutator `pcc_gc_step()`.

Validation:

```text
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 300 \
  uv run pytest tests/python/test_gc_backend_concurrent.py -q -n0 -rxX

6 passed in 25.03s

env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 420 \
  uv run pytest tests/python/test_pcc1_threading_gc_runtime.py -q -n0

6 passed, 1 skipped in 31.25s

PCC_PCC1_THREADED_GC_STRESS_RUNS=20 \
PCC_PCC1_THREADED_GC_STRESS_BACKENDS=2 \
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 420 \
  uv run pytest \
  tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threaded_explicit_gc_repeated_runs_stress \
  -q -n0

1 passed in 5.48s
```
