# Investigation: pcc1 real pthread runtime hangs under explicit threaded gc.collect

## Status
resolved

Backend 0..4 real-pthread explicit `gc.collect()` are covered by a pcc1 hard
gate. Backend 2's previous concurrent-GC `py_list_append` abort was
root-caused to container temporary roots being unpinned too early in threaded
functions.

## Problem Description
The task list requires pcc1 to prove multithreaded capability, GC behavior
under multithreading, and concurrent-GC reliability. While adding a pcc1 hard
gate that forces the C runtime archive with `PCC_WITH_THREADS=1`, a
pcc1-compiled Python program using real `Thread` / `Lock` can run correctly
without explicit collection, but hangs when worker threads and the main thread
perform repeated `gc.collect()` calls.

Resolved by making the threaded runtime's blocking boundaries
safepoint-aware:

- `pcc_mutex_lock()` now polls contended mutex acquisition and calls
  `pcc_thread_safepoint()` while waiting.
- `pcc_thread_join()` now waits for the runtime thread-completion flag while
  calling `pcc_thread_safepoint()` instead of blocking directly in
  `pthread_join()`.
- backend 0 `py_gc_collect()` now parks at a safepoint when another thread
  already owns stop-the-world, matching the retry behavior used by the
  nonzero GC backends.

## Repro
The reliable hard gate now lives in:

```bash
env -u LC_ALL uv run pytest tests/python/test_pcc1_threading_gc_runtime.py -q -n0
```

Expected fixed result:

```text
2 passed in 8.37s
```

The passing case is:

```text
tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threads_lock_backend0
```

It builds with:

```text
PCC_RUNTIME_CC=cc
PCC_RUNTIME_HIGH=c
PCC_WITH_THREADS=1
PCC_GC_BACKEND=0
```

and confirms that a pcc1-compiled real-pthread `Thread` / `Lock` program
prints `4000` on repeated runs.

The explicit threaded-GC proof is now a hard gate:

```text
tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threads_and_explicit_gc_collect_backend0
```

The failing shape was:

```python
import gc
from threading import Lock, Thread

counts = [0]
lock = Lock()

def worker() -> None:
    i = 0
    while i < 200:
        chunk = [i, i + 1, i + 2]
        if i % 23 == 0:
            gc.collect()
        lock.acquire()
        counts[0] = counts[0] + (chunk[1] - chunk[0])
        lock.release()
        i = i + 1

def main() -> None:
    t0 = Thread(target=worker)
    t1 = Thread(target=worker)
    t2 = Thread(target=worker)
    t3 = Thread(target=worker)
    t0.start()
    t1.start()
    t2.start()
    t3.start()
    i = 0
    while i < 20:
        gc.collect()
        i = i + 1
    t0.join()
    t1.join()
    t2.join()
    t3.join()
    gc.collect()
    print(counts[0])
```

Compiled by pcc1 with the C runtime archive and `PCC_WITH_THREADS=1`, the
produced binary timed out after 30 seconds when run under backend 0 and backend
2 in local probes.

## Findings
- pcc1 can compile and run real-pthread `Thread` / `Lock` code when explicit
  collection is not interleaved with active threads.
- The pcc-Python runtime archive path is not enough for this proof because its
  `Thread.start()` is still a synchronous shim.
- The hang is not limited to backend 2; the default backend 0 also hung in the
  explicit threaded `gc.collect()` shape when forced through the C runtime
  archive.
- Root causes:
  - `pcc_mutex_lock()` used blocking `pthread_mutex_lock()`. If a thread was
    blocked there while another thread initiated stop-the-world collection,
    the blocked thread had no opportunity to report a safepoint.
  - `pcc_thread_join()` used blocking `pthread_join()`. If a worker thread
    initiated collection while the main thread was waiting in `join()`, the
    main thread could not park.
  - backend 0 `py_gc_collect()` returned immediately when another thread
    already owned stop-the-world, instead of safepointing until the owner
    resumed the world.
- Reduction probes under backend 0:
  - main-thread `gc.collect()` while workers run, but workers do not collect:
    passed and printed `800`.
  - worker-thread `gc.collect()` while main only joins: passed and printed
    `800`.
  - both main and worker threads perform explicit `gc.collect()`, with no
    final post-join collect: exited 0 but produced no output.
  This points at overlapping explicit collection across multiple Python
  threads, not basic pthread startup or Lock behavior.
- Existing runtime substrate tests cover STW gates and lower-level pthread
  behavior, but they did not prove this pcc1-compiled Python-level shape.

## Next Steps
- Keep the backend 0..4 explicit-collect pcc1 hard gate green.
- Treat statistical stress of repeated pcc1-built threaded explicit
  `gc.collect()` runs as a separate concurrent-GC reliability task. The hard
  gate proves the supported shape across every backend; it is not a long-run
  flake detector.

## Update 2026-05-14: tracing backend follow-up

Backend 0 remains green:

```bash
env -u LC_ALL uv run pytest tests/python/test_pcc1_threading_gc_runtime.py -q -n0
# 2 passed in 6.87s
```

The same real-pthread explicit-collect shape was probed manually under backend
1 and backend 2 with the C runtime archive (`PCC_RUNTIME_CC=cc`,
`PCC_RUNTIME_HIGH=c`, `PCC_WITH_THREADS=1`). Both tracing backends still fail
intermittently: backend 1 produced `<null>` output and occasional
`py_list_append` assertions; backend 2 produced `<null>` output and `SIGABRT`.
Backend 3 and backend 4 did not fail in a short five-run probe, but they are
not yet hard gates for this shape.

Narrow fixes landed during the investigation:

- C and pcc-Python frame-root registration now hold the GC object graph lock
  while mutating the global frame-root list.
- Tracing finalization now rescans current roots under STW and drains newly
  grayed objects before the white-object cut.
- Tracing finalization and tracing sweep now decline to proceed if they cannot
  acquire STW.
- Backend 1/2 allocations made during an active mark phase are colored black so
  they survive the current cycle.
- Backend 1/2 allocation-triggered auto-progress no longer starts a brand-new
  tracing cycle under real threads, and fresh allocations are protected for one
  tracing cycle.

Those fixes are necessary but not sufficient. A 20-run probe after these
repairs still produced intermittent `<null>` output under backend 1 and backend
2, with occasional `py_list_append` assertions under backend 1. The remaining
root cause is the allocation-time tracing seam: `pcc_gc_alloc()` calls
`pcc_gc_note_alloc()` before the newly allocated object exists and before
surrounding compiler temporaries are stored into GC frame slots. If backend 1/2
tracing observes a list/dict/tuple literal while it is still being constructed
in SSA temporaries, the collector can white-cut an object that is about to be
stored into a root. Backend 2's worker has the same class of risk.

Next design task: move backend 1/2 tracing progress to explicit safepoints or
add a temporary-root interface around object construction. Do not promote
backend 1/2 real-pthread explicit-collect probes to hard gates until that seam
is fixed.

## Update 2026-05-14: backend 1 stabilized, backend 2 still active

Follow-up current-source host `pcc` probes isolated two additional root causes:

- `pcc_stop_the_world()` needed same-thread reentrancy because explicit
  `pcc_gc_collect()` can hold an outer STW boundary while tracing finish/sweep
  performs an inner STW transition.
- Container literal temporary roots were leaving too early. The generated IR
  cleared and left the list/dict/tuple construction root before storing the
  expression result into the assignment target slot, leaving a cross-thread
  STW window where another thread could sweep a just-constructed container.

The current fix changes function-local container temporary roots into
entry-frame roots. Literal construction still pins during population, but the
root slot now remains live until the next overwrite or function cleanup instead
of being cleared before the assignment store. Sweep also skips pinned/fresh
objects so a construction-time pin/fresh allocation acts as a real sweep
barrier.

Focused reductions under backend 1 now pass:

```text
after_join_only:      0/30 failures
main_while_workers:  0/30 failures
worker_only:         0/30 failures
```

The original current-source host `pcc` probe also passed under backend 1:

```text
backend=1 fail_count=0/100
```

Backend 2 remains active. Even after disabling threaded-mode CMS mutator
assist, worker startup, worker tracing, and CMS write-buffer enqueueing, the
same probe still intermittently aborts in `py_list_append`:

```text
backend=2 fail_count=1-3/100
```

Conclusion:

- Backend 1 threaded explicit `gc.collect()` is stable in the current-source
  host probe, but still needs a rebuilt pcc1 proof before becoming a hard
  pcc1 gate.
- Backend 2 must remain a separate concurrent-GC reliability task. Treating it
  as fixed would be dishonest; the remaining failure is backend2-specific and
  not explained by the now-fixed generic container temporary-root gap.

Validation after the final import/threading-gated lowering change:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 58.32s

tests/python/test_pcc1_threading_gc_runtime.py
tests/python/test_gc_threading_substrate.py
14 passed in 9.49s

tests/python/test_fallback_baseline.py
tests/python/test_ir_py_fallback_baseline.py
15 passed in 54.06s

current-source host pcc backend=1 explicit-threaded-collect probe
backend=1 fail_count 0/30
```

## Update 2026-05-14: all GC backends in the pcc1 real-pthread hard gate

The real-pthread pcc1 gate now covers every runtime GC backend:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 300 \
  uv run pytest tests/python/test_pcc1_threading_gc_runtime.py -q -n0
# 6 passed in 21.13s
```

This extends `test_pcc1_c_runtime_threads_and_explicit_gc_collect_*` from
backend 0/1/2 to backend 0/1/2/3/4 while still forcing:

```text
PCC_RUNTIME_CC=cc
PCC_RUNTIME_HIGH=c
PCC_WITH_THREADS=1
```

That means the compiled program uses the C runtime archive and real pthread
`Thread` / `Lock`, not the pcc-Python runtime's synchronous thread shim.

During the extension pass, one full-file run observed transient `SIGABRT`
exits under backend 0 and backend 2. Backend-specific reruns passed, and the
backend 2 failing executable then completed 30 direct runs. Do not treat this
hard gate as a statistical stress proof; the separate task for pcc1
multi-threaded / concurrent-GC reliability should add a dedicated repeated-run
stress harness with crash diagnostics.

The fallback baseline now forces `PCC_PYTHON_IR_PASSES=off` for its raw
multi-file fallback-count probe. The raw multi-file closure compiles in about
11 seconds with passes off, while the Python IR pass batch subprocess can hang
for more than 300 seconds on the same very large closure IR. That pass-pipeline
performance/stability issue is separate from the fallback-count ratchet.

## Update 2026-05-14: backend 1 promoted to pcc1 hard gate

Backend 1 now has a rebuilt pcc1 proof, not just a current-source host probe.
`tests/python/test_pcc1_threading_gc_runtime.py` parameterizes the existing
real-pthread explicit-collection gate over `PCC_GC_BACKEND=0` and `1`.

Validation:

```text
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 420 \
  uv run pytest \
  tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threads_and_explicit_gc_collect_backend01 \
  -q -n0

2 passed in 7.02s
```

This backend 2 note was superseded later the same day; see the next update.

## Update 2026-05-14: backend 2 promoted to pcc1 hard gate

Backend 2 no longer remains an active gap for this pcc1 real-pthread explicit
`gc.collect()` shape. The failing reduction was:

- module-global `results = []`;
- worker threads repeatedly build a list literal;
- worker threads and the main thread call `gc.collect()`;
- workers append a value derived from the list literal into `results`.

Before the fix this intermittently aborted in `py_list_append` because a
threaded function's container temporary root was unpinned at expression end and
then relied only on the frame-root chain while another thread performed an
explicit backend2 collection. The fix keeps persistent threaded container
temporary roots pinned until the function cleanup path, unpins the previous
slot value before overwriting it, and makes `pcc_gc_pin(NULL)` /
`pcc_gc_unpin(NULL)` no-ops for pin-balance correctness. Module-global and
native-module attribute roots now also pin their stored object values
symmetrically.

Validation:

```text
current-source host pcc backend2 results-only stress probe
PASS host backend2 results_only runs=100

env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 900 \
  uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0

1 passed in 57.58s

env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest tests/python/test_pcc1_threading_gc_runtime.py -q -n0

4 passed in 14.58s

env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 300 \
  uv run pytest \
  tests/python/test_fallback_baseline.py \
tests/python/test_ir_py_fallback_baseline.py \
  -q -n0

16 passed in 58.42s
```

## Update 2026-05-14: repeated-run pcc1 stress harness and stale sweep candidates

An opt-in repeated-run stress harness now lives in:

```text
tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threaded_explicit_gc_repeated_runs_stress
```

It is disabled by default to keep the normal hard gate bounded. Enable it with:

```bash
env -u LC_ALL -u LC_CTYPE \
  PCC_PCC1_THREADED_GC_STRESS_RUNS=20 \
  PCC_PCC1_THREADED_GC_STRESS_BACKENDS=2 \
  perl -e 'alarm shift; exec @ARGV' 420 \
  uv run pytest \
  tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threaded_explicit_gc_repeated_runs_stress \
  -q -n0
```

The first run of the stress harness reproduced the previous flake immediately:

```text
backend=2 run=1 exit=-6
Assertion failed: (l->h.type_tag == PY_TYPE_LIST), function py_list_append
```

Two reductions split the failure:

- `no_chunk_results`: removes the worker-local `chunk = [...]` list literal
  and appends a constant to the module-global result list; passed 20/20.
- `chunk_no_results`: keeps `chunk = [...]` and writes only to a counter under
  the lock; failed at run 12/20 in `py_list_append`.

So the root was not the module-global `results` root. It was the threaded list
literal temporary root path.

The root cause was stale tracing sweep candidates. Backend 1/2
`pcc_gc_sweep_unreachable()` skipped objects protected by `PY_FLAG_GC_PINNED`
or `PY_FLAG_GC_FRESH_ALLOC`, but left `PY_FLAG_GC_SWEEP_CANDIDATE` set. A
worker-local container literal can be protected by the persistent temporary
root during one sweep, then later be unpinned while still reachable through a
frame/local-owned slot. A later sweep would see the stale candidate bit and
free the object anyway, which eventually surfaced as `py_list_append()` seeing
a non-list object.

The fix is to clear `PY_FLAG_GC_SWEEP_CANDIDATE` when sweep skips an object
because it is pinned or fresh. The C runtime and pcc-Python mirror now share
that behavior.

Validation:

```text
PCC_PCC1_THREADED_GC_STRESS_RUNS=3, backends=0..4
1 passed in 17.43s

PCC_PCC1_THREADED_GC_STRESS_RUNS=20, backends=2
1 passed in 5.44s

PCC_PCC1_THREADED_GC_STRESS_RUNS=20, backends=1
1 passed in 3.69s

env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 420 \
  uv run pytest tests/python/test_pcc1_threading_gc_runtime.py -q -n0

6 passed, 1 skipped in 21.39s
```

Operational note: these tests mutate or rebuild the runtime archive. Do not
run them in parallel with other runtime-archive GC tests; doing so can produce
false pcc1 failures from archive races rather than from the generated program.

## Update 2026-05-14: backend2 worker restored without regressing pcc1

The broader backend2 CMS worker gate is now compatible with the pcc1
real-thread explicit-`gc.collect()` gate. Backend2 worker work now stops the
world before tracing object internals, then takes the object-graph lock. That
preserves worker start/drain/trace/lifecycle coverage while avoiding races
against pcc1-generated list/dict mutation.

Validation:

```text
tests/python/test_gc_backend_concurrent.py
6 passed in 25.03s

tests/python/test_pcc1_threading_gc_runtime.py
6 passed, 1 skipped in 31.25s

PCC_PCC1_THREADED_GC_STRESS_RUNS=20, backend=2
1 passed in 5.48s
```

## Update 2026-05-14: transient backend2 append abort observed, gate rerun green

During the later pcc1 GC-effectiveness audit, the full threaded runtime gate
observed one backend2 abort:

```text
tests/python/test_pcc1_threading_gc_runtime.py
backend=2
exit=-6
Assertion failed: (l->h.type_tag == PY_TYPE_LIST), function py_list_append,
file py_list.c, line 194.
```

Immediate follow-up evidence:

```text
manual backend2 pcc1-built binary: 10 direct runs
800
800
800
800
800
800
800
800
800
800

PCC_PCC1_THREADED_GC_STRESS_RUNS=30
PCC_PCC1_THREADED_GC_STRESS_BACKENDS=2
tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threaded_explicit_gc_repeated_runs_stress
1 passed in 4.75s

PCC_PCC1_THREADED_GC_STRESS_RUNS=100
PCC_PCC1_THREADED_GC_STRESS_BACKENDS=2
tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threaded_explicit_gc_repeated_runs_stress
1 passed in 5.99s

tests/python/test_pcc1_threading_gc_runtime.py
6 passed, 1 skipped in 24.06s
```

Interpretation:

- The normal hard gate and the backend2 stress gate are currently green.
- The abort is still relevant residual risk because it is the same failure
  signature as the earlier stale-sweep-candidate bug.
- The next time it reproduces, capture it under LLDB at the failing
  `py_list_append` call and inspect the `lst` object header (`type_tag`,
  flags, and whether the address still exists in `pcc_gc_objects`). Do not
  paper over it by relaxing the test.

## Update 2026-05-14: backend0 abort root-caused to missing frame roots

The pcc1 real-pthread explicit-`gc.collect()` matrix later exposed an
intermittent backend0 abort:

```text
tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threads_and_explicit_gc_collect_all_backends[0]
pcc1-built threaded explicit-GC binary failed (exit -6)
```

The direct pcc1-built backend0 binary reproduced within 9 to 33 runs. macOS
crash reports showed `malloc` aborting under `py_decref()` while the generated
worker released the previous `chunk` list. An ASan runtime build produced the
decisive stack:

```text
heap-use-after-free in py_gc_untrack py_obj_gc.c:571
read by T1: py_decref -> user_pcc_thread_explicit_gc_worker
freed by T3: py_gc_collect py_obj_gc.c:520
allocated by T1: pcc_gc_alloc -> py_list_new -> worker
```

Root cause:

- backend0's CPython-style cycle collector relied only on refcounts to
  identify external roots.
- pcc-generated stack/frame roots can hold live container temporaries across a
  cross-thread stop-the-world window.
- Another thread's backend0 `gc.collect()` could therefore classify a list
  still present in a pcc frame root as unreachable and free it.
- The original owner thread later released its normal stack reference and
  touched freed memory in `py_gc_untrack()`.

Fix:

- Added `pcc_gc_visit_runtime_roots()` so backend0 can mark pcc frame roots
  and scheduler roots during `py_gc_recompute_reachability()`.
- backend0 now also treats pinned tracked objects as roots during the same
  recompute pass.
- Mirrored the root marking in `pcc/py_runtime/py/py_obj_gc.py`.
- backend0 frame-root registration is enabled only when `PCC_WITH_THREADS=1`;
  single-thread backend0 keeps the old low-overhead path. This matters for
  self-bootstrap because unconditional pcc-Python backend0 frame tracking made
  stage2 too slow and initially exposed stale-runtime archive crashes.
- Fixed the C debug helper so `PCC_DEBUG_RUNTIME=1` does not abort on the
  valid no-op `py_decref(NULL)`.

Validation:

```text
/tmp pcc1-built backend0 threaded explicit-GC binary
120 direct runs passed, every run printed 800

env -u LC_ALL -u LC_CTYPE uv run python -m py_compile \
  pcc/py_runtime/py/py_obj_gc.py pcc/py_runtime/py/py_gc_backend.py

env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_pcc1_threading_gc_runtime.py -q -n0 -rxX
6 passed, 1 skipped in 24.04s

env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0
1 passed in 97.76s

env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py -q -n0
17 passed in 62.15s
```

Operational note: after changing pcc-Python runtime-high files, delete stale
`libpy_runtime_pcc_py*.a` archives before judging bootstrap crashes. One
intermediate run crashed stage2 using a stale archive from the unconditional
backend0 frame-tracking experiment; a clean archive rebuild made the
self-bootstrap gate pass.
