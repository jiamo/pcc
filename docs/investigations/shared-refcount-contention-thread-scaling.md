# Investigation: shared-object refcount contention destroys thread scaling; gc.immortalize restores it on backend 0

## Status

active

## Problem Description

The Quansight post "Scaling NumPy on Free-Threaded Python"
(labs.quansight.org/blog/scaling-numpy-on-free-threaded-python, mirrored by
numpy/numpy#30494) diagnoses why NumPy workloads scale badly on free-threaded
CPython: every thread increfs/decrefs the same shared singletons (dtype
objects, module objects), so atomic refcount traffic serializes on one cache
line; CPython's fix is an immortalization escape (`PyUnstable_Object_SetImmortal`)
that NumPy applies to its singletons. The user asked whether pcc1 solves this
problem and to demonstrate pcc's advantage on it.

pcc's runtime had the mechanism but not the surface: `py_incref`/`py_decref`
early-return on `PY_FLAG_IMMORTAL` (pcc/py_runtime/src/py_obj.c:710,879), but
nothing user-facing could set the flag, and no benchmark measured the
contention. The existing parallelism proof
(tests/python/test_boc_threading_proof.py, ~3.57x on 4 threads) uses
independent per-thread data, so it never exercises the blog's bottleneck.

## Repro

Benchmarks (plain Python, also runnable under CPython):

- benchmarks/python/shared_refcount_contention.py — 4 threads, each does 5M
  iterations of `s = touch(SHARED); acc += s.v` where `touch` returns its
  borrowed parameter (callee retains, caller releases: one incref/decref pair
  per iteration on ONE shared instance).
- benchmarks/python/shared_refcount_contention_serial.py — same total work
  (20M iterations) on one thread.
- benchmarks/python/shared_refcount_contention_immortal.py — parallel variant
  plus `gc.immortalize(SHARED)` before the threads start.

Compiled with `PCC_WITH_THREADS=1`, `PCC_RUNTIME_CC=cc`, threaded runtime
archive (tests/runtime_build_cache.cached_threaded_c_runtime), ir_scaffold=on,
libpython=off; run per backend via `PCC_GC_BACKEND=0..4`; min of 2 runs.

## Test [CONFIRMED]

Measured 2026-08-07 on macOS arm64 (M-series), speedup = serial / parallel:

```text
 backend   serial  parallel      sp  immortal      sp
       0    1.59s     4.88s   0.33x     0.38s   4.20x
       1    3.44s     3.98s   0.86x     3.41s   1.01x
       2    3.44s     4.03s   0.85x     3.48s   0.99x
       3    3.45s     3.99s   0.87x     3.49s   0.99x
       4    4.13s     5.15s   0.80x     4.49s   0.92x
```

Reference points:

- CPython 3.14 (GIL build), same sources: serial 0.86s, parallel 0.90s,
  speedup 0.95x — threads give nothing.
- pcc backend 0 + immortal parallel wall-clock 0.38s is 2.3x faster than
  CPython's best (0.86s serial) and 4.20x its own serial.
- Control benchmark boc_bank_demo.py (no shared object): ~3.5x, so the
  gap to 0.33x is attributable to the shared-object traffic.
- Correctness: all 4 workers print acc=35000000 in every configuration.

Confirmed readings:

1. Backend 0 (atomic refcount) reproduces the blog's disease exactly:
   parallel 3x SLOWER than serial (0.33x).
2. `gc.immortalize(SHARED)` cures backend 0 completely (4.20x, near-linear,
   superlinear vs the contended baseline because the refcount pair vanishes
   from the hot loop on every thread).
3. Backends 1-4 do not scale even with the immortal flag (~1.0x): the
   remaining serialization is NOT the object's refcount. Their serial
   baseline is also ~2x slower than backend 0. The prime suspect is the
   per-call GC frame enter/leave bookkeeping against the shared frame-index
   structure — see gc-frame-index-entry-pool-perf.md (frame roots need the
   hash; cost is per-frame malloc). This is a separate, pre-existing perf
   thread, deliberately not folded into this investigation.

## Proposals

- No.1 Expose the immortal bit as `gc.immortalize(obj)`   [CONFIRMED]
- No.2 Remove backend 1-4 frame-index serialization        [pending — belongs
  to gc-frame-index-entry-pool-perf.md, tracked there]

## No.1 Expose the immortal bit as `gc.immortalize(obj)`

### Code Change

pcc's native equivalent of CPython 3.14's `PyUnstable_Object_SetImmortal`
(the exact API the blog's NumPy work needed), four small pieces:

- pcc/py_runtime/src/py_obj.c: `pcc_gc_immortalize(o)` — null/tagged-int
  guard, `pcc_gc_pin(o)` (so moving backends #3/#4 never relocate it), then
  `py_header_flags_or(h, PY_FLAG_IMMORTAL)`. Declared in
  pcc/py_runtime/include/py_runtime.h next to pin/unpin.
- pcc/py_runtime/py/py_obj.py: differential-equal port mirror
  (`@c_abi_export("pcc_gc_immortalize")`, pin + `flags | 1` via
  load_i32/store_i32 at offset 12, constants inlined per port rules).
- pcc/py_frontend/codegen/runtime_abi.py: `"pcc_gc_immortalize":
  (_VOID, [_PYOBJ], False)`.
- pcc/py_frontend/codegen/native_gc.py: `gc.immortalize` branch modeled on
  `gc.is_tracked` (`_emit_as_object(args[0])`, returns None literal).

Semantic boundary (documented in the C comment): immortalize is an opt-in
for process-lifetime singletons; the object is never deallocated afterwards.
It does not untrack the object from tracing GC.

### CONFIRMED

- Scaling: the matrix above (0.33x -> 4.20x on backend 0).
- Regression home: tests/python/test_gc_immortalize.py
  (`2 passed in 1.11s`, `-n0`): a `__del__` canary must NOT fire for an
  immortalized object after its last reference is dropped while a mortal
  sibling still finalizes, and 4 threads over an immortalized shared
  instance compute exact results under PCC_WITH_THREADS=1.
- Native gc surface unharmed: tests/python/test_gc_api.py `16 passed`.

## Open boundaries

- Backends 1-4 thread-scaling cap (~1.0x) — owned by
  gc-frame-index-entry-pool-perf.md, not closed here.
- Bootstrap gates for the runtime_abi/native_gc/runtime touch have not been
  run in this session (long-run authorization pending); the focused gates
  above are green. Mode label: host-pcc compiled binaries, C runtime tier
  (PCC_RUNTIME_CC=cc), threaded archive; no pcc1/pcc2 claim is made.
- The benchmark loop initially could not run at all: 5M-iteration hot loops
  die of a per-iteration call.ret.root alloca — a distinct codegen bug with
  its own file, see
  py-frontend-call-ret-root-alloca-loop-stack-overflow.md. The matrix above
  was measured with that fix applied experimentally.
- Real `import numpy` under pcc is a separate B-track boundary (C-extension
  ABI); this investigation proves the mechanism-level claim on the blog's
  bottleneck #1, not NumPy execution.
