# Investigation: threaded GC safepoint production blocker

## Status

active

## Problem Description

`pcc_stop_the_world()` is cooperative: the collector can only stop other live
mutator threads after those threads enter `pcc_thread_safepoint()`. Before this
investigation, `pcc_gc_alloc()` did not poll the thread safepoint, so an
allocation-heavy worker loop could keep running without ever parking for STW.

This is a shared blocker for backend #0-#4 GC and for Loom-shaped virtual
threads. The scheduler and the collector both need bounded yield points.

## Findings

- `pcc_gc_safepoint()` already calls `pcc_thread_safepoint()`.
- `pcc_gc_alloc()` was not a safepoint.
- Calling the full `pcc_gc_safepoint()` from `pcc_gc_alloc()` would be wrong:
  non-threaded builds would run `pcc_gc_step(1)` on every allocation.
- The safe first slice is to call `pcc_thread_safepoint()` from `pcc_gc_alloc()`.
  In non-threaded builds that symbol is the existing no-op implementation, so
  the allocation path does not need an `extern -> compare -> branch` guard.

## Implemented Slice

Allocation boundary is now an implicit safepoint for threaded builds:

- C runtime: `pcc/py_runtime/src/py_obj.c`
- pcc-Python mirror: `pcc/py_runtime/py/py_obj.py`

This covers allocation-heavy mutator loops. It does not cover pure i64 compute
loops; those still require codegen loop-backedge and function-entry safepoints.

## Regression Coverage

`tests/python/test_gc_threading_substrate.py` now checks:

- `pcc_gc_alloc()` polls `pcc_thread_safepoint()` in both C and pcc-Python
  runtimes.
- `pcc_gc_alloc()` does not call full `pcc_gc_safepoint()` or `pcc_gc_step()`
  in the allocation path.
- A `PCC_WITH_THREADS=1` worker loop that only allocates can still let the main
  thread complete repeated STW/resume cycles.

## Remaining Work

- Add pcc1-generated threaded-program coverage, not only host C runtime
  coverage.
- Evaluate runtime-call boundary safepoints if allocation, function-entry, and
  loop-backedge coverage is still too sparse.
- Measure self-bootstrap and hot-loop cost from unconditional no-op polls in
  non-threaded builds; if needed, lower the helper to a TLS flag branch plus
  slow call.

## Update: codegen loop/function-entry safepoints

The first codegen slice is implemented:

- `CoreHelperMixin._emit_thread_safepoint()` centralizes the runtime call.
- `while` lowering now uses a latch block as the `continue` target; the latch
  polls `pcc_thread_safepoint()` before branching back to the condition.
- `for` lowering now polls at every step/latch path, including range, list,
  tuple, dict/dyn iterator, native iterator, async iterator, CPython iterator,
  and comprehension loops.
- typed-int low-IR while loops insert a runtime safepoint before the back-edge,
  and low-IR functions poll once at entry.
- user functions, class methods, generator wrappers/resume functions, module
  top-init, and program main poll once at entry.

This closes the pure-compute-loop structural hole at the generated-IR level.
It is still a cooperative safepoint model, not signal-based preemption.

## Update: pcc1-generated pure-compute threaded gate

The pcc1 hard gate now includes a real C-runtime pthread program compiled by
pcc1 where the worker enters a long i64-only loop after setting a ready flag.
The worker hot loop performs no allocation, lock acquisition, print, or
explicit runtime call; the main thread performs repeated `gc.collect()` calls
while the worker is active. This directly proves the generated loop-backedge
safepoint, rather than relying on allocation or mutex boundaries.

The gate runs for backend #0 and backend #4 because backend #0 is the default
STW reference and backend #4 is the active moving-GC target:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_pure_compute_loop_safepoints_under_threaded_gc \
  -q -n0
```

The first backend #4 run of this gate exposed a separate moving-GC invariant:
class method-table entries are raw generated callable/code pointers, not
heap-object slots. Backend #4 class lookup/relocation/trace/promotion now
leaves `PyClassObject.methods[i].func` and `del_method` out of the read-barrier
and root-visitor protocol while preserving barriers for bases, MRO, and attrs.

Current validation:

- `tests/python/test_pcc1_gc_backend_matrix.py::test_pcc1_self_backend_compile_smoke_under_gc_backend[4]`
  passes with the rebuilt pcc1.
- `tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_pure_compute_loop_safepoints_under_threaded_gc`
  passes for backend #0 and backend #4.
