# Freestanding incremental/concurrent GC scheduler evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns the backend-1/2 scheduling
closure: allocation debt and budget policy, bounded tracing-cycle progress,
debt discharge, pause telemetry, backend-1 auto-step, backend-2 CMS
start/stop/assist accounting, explicit-pause reporting, and the public
`pcc_gc_note_alloc` entrypoint.

The managed `pcc_gc_step` dispatcher delegates backend 1/2 immediately to the
strict scheduler while retaining its backend-3 promotion and backend-4
relocation branches.  Generational promotion/oldification and relocating
policy/remap remain open, so `LIBC-P2-FREESTANDING-GC` remains `DONE_WEAK`.

## Ownership and preserved policy

`freestanding_gc_incremental_concurrent_scheduler.py` exports exactly eleven
raw scheduling symbols.  LLVM and self emission produce the same finite
undefined closure, and production archive
`e2f0eb92061a87528e321941-pcc-py/libpy_runtime_pcc_py.a` reports every export
exactly once from `freestanding_gc_incremental_concurrent_scheduler.o`.

The migration preserves the historical safety verdict from
`gc-backend1-auto-step-sweep-debt.md`: allocation-time auto-step may finish a
mark cycle and clear debt, but does not sweep candidates.  It also preserves:

- configured live-byte/pause debt thresholds and bounded work budgets;
- one work-step and pause observation per backend-1/2 step;
- debt discharge followed by the completed-cycle debt reset;
- no backend-1 auto-step when threads are enabled;
- CMS worker-start accounting before allocation assist;
- backend-3/4 use of the same strict bounded tracing-cycle primitive only from
  their existing managed dispatcher branches.

Ordinary Python floor division initially emitted a `ZeroDivisionError` path and
was rejected by the strict validator.  The final source uses the existing raw
`unsigned_div_i64` intrinsic only where the caller proves positive operands and
nonzero constant divisors; the fail-closed managed-reference rule was not
weakened.

## Focused gates

```text
tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py
  5 passed in 58.46s

tests/python/test_gc_backend_incremental.py + test_gc_backend_concurrent.py
  14 passed in 71.03s

tests/python/gc/test_gc_backend_config_fastpath.py + test_gc_abstraction_surface.py
  22 passed in 5.45s

five-backend lifetime/finalizer/resurrection/weakref + referent/slot + backend4 production
  189 passed in 22.06s

tests/python/test_pcc1_threading_gc_runtime.py
  9 passed, 1 deselected in 47.39s
```

The TDD observations were:

```text
strict source absent
  1 failed in 0.08s (FileNotFoundError)

first strict compile
  1 failed in 0.33s (py_exc_new from Python // semantics)

raw unsigned-division implementation
  1 passed in 0.59s
```

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-incremental-concurrent-scheduler-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-incremental-concurrent-scheduler-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=17651 \
  output=build/freestanding-gc-incremental-concurrent-scheduler-stage1/pcc1
```

The profile records 16.478 seconds.  `file` reports arm64 Mach-O and `otool -L`
reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That pcc1 compiled
the real strict scheduler with `--ir-scaffold=on --backend self
--python-libpython off --python-library`; clang accepted the IR, all eleven
exports are definitions, and no `call` or `invoke` targets `py_cpy_*`.

## Scoped hashes

```text
f1cbea3aa9e9b3f7a433397eb20792ac21f38de4f3890423a8534814c191159b  pcc/py_runtime/py/freestanding_gc_incremental_concurrent_scheduler.py
c3c65f2dff8b8641e8d8fd835c0d81a3dac4a5e3a2df635b78a957bd9333e743  pcc/py_runtime/py/py_gc_backend.py
dba56b407793f318b121e115ca76daf021d93555a6bad6299f70b53f62c9d179  pcc/py_frontend/codegen/runtime_abi.py
78d48eadc523b7c75d7a0c26d8f6610b7e8c1dbf54888ad53b6b33418f78814d  pcc/py_runtime/Makefile
6d9104b844817b191498b0c7311fdebafea98b23d668b14ca745736c1ea1d304  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py
d28e035a073f328309118d4af340703be9f8f50284b3fb038f889db5f9de8856  tests/python/gc/test_gc_backend_config_fastpath.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move generational promotion/oldification next, then relocating policy/remap.
After every production GC symbol has a strict pcc-Python owner, prove no
production GC C object is linked, run the full five-GC semantic/fixed-point
matrix once, and record long-running RSS/fragmentation/pause/throughput deltas.
