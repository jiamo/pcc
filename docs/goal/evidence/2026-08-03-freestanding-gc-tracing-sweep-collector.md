# Freestanding tracing sweep collector evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns the common GC1..4 raw sweep
kernel: candidate discovery, PASS-0 finalizer dispatch, PEP 442 resurrection
re-mark, and PASS-2 type-specific deallocation around the previously migrated
PASS-1 clear phase.  The managed `py_gc_backend.py` public wrappers cross only
the two raw candidate/sweep ABIs.

This closes the common tracing sweep/finalizer/deallocation state machine for
`LIBC-P2-FREESTANDING-GC`.  Configuration-aware public wrappers and the
incremental/concurrent, generational and relocating policies remain outside
this slice, so the task remains `DONE_WEAK`.

## Ownership and closure

`freestanding_gc_tracing_sweep_collector.py` exports exactly four raw phases:

```text
pcc_gc_tracing_has_sweep_candidate
pcc_gc_tracing_finalize_unreachable
pcc_gc_tracing_recheck_reachability_after_finalizers
pcc_gc_tracing_sweep_unreachable
```

LLVM and self objects have the same exact finite function/global closure,
pinned by the focused test.  The current production archive
`da8f9cdb983dee8be5ee783b-pcc-py/libpy_runtime_pcc_py.a` reports every export
exactly once from `freestanding_gc_tracing_sweep_collector.o`; none is defined
by `py_gc_backend.o`.

The source and runtime gates preserve these correctness boundaries:

- PASS-0 runs finalizers while fields are intact.
- a second root mark clears candidate bits from resurrected objects before
  PASS-1;
- PASS-1 clears up to the budget without freeing sibling candidates;
- PASS-2 finalizes the same bounded candidate set;
- pinned/fresh and C-extension-owned objects keep their existing guards;
- the deallocating bit is published before any safepoint-capable deallocator;
- backend-4 zpage objects delay their freeing notification until after the
  type-specific deallocator.

## Focused gates

```text
tests/python/test_freestanding_gc_tracing_sweep_collector.py
  5 passed in 58.01s

tests/python/test_gc_update_referents.py
  31 passed in 0.54s

tests/python/test_gc_backend4_production.py
  128 passed in 10.17s

five-backend object lifetime + finalizer cycle/resurrection + weakref callback/finalizer
  30 passed in 60.90s
```

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-tracing-sweep-collector-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-tracing-sweep-collector-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=35369 \
  output=build/freestanding-gc-tracing-sweep-collector-stage1/pcc1
```

The profile records 34.169 seconds.  `file` reports arm64 Mach-O and `otool -L`
reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That pcc1 compiled
the real strict collector with `--ir-scaffold=on --backend self
--python-libpython off --python-library`; clang accepted the IR, all four
exports are definitions, and no `call` or `invoke` targets a `py_cpy_*` symbol.

## Scoped hashes

```text
0942d041bb6bcf78dac7aab76096a12b3b9e6f3ec1dc5a73f69dfb2a50de8ed5  pcc/py_runtime/py/freestanding_gc_tracing_sweep_collector.py
8a1a86a569c9920bbee927c68f0cc9561773f7ec39c29ac0ad82e4af311f695b  pcc/py_runtime/py/py_gc_backend.py
ce5b67999775bfb0980f0940f8fc59b58cca522e2e6b6a873dfbb56a099bd8d9  pcc/py_frontend/codegen/runtime_abi.py
215feaf7b805c8efcc3727f03ad68313cc00c3f8bdd2e494dc1cd1206f79ced1  pcc/py_runtime/Makefile
2e63616f908afdc5773bca860c411b0eeef060215ccead784aac9c35325ef6ad  tests/python/test_freestanding_gc_tracing_sweep_collector.py
c522b6127bcae9d089ed3a499f071f92de90fff6e36e8db781df3ef2c9a8ecda  tests/python/test_gc_update_referents.py
bc5924aeaaf771b0aaa0abd77fed4b76b60c0fb3030577189a5780e6fbe2361c  tests/python/test_gc_backend4_production.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move the config-aware public collection wrapper boundary, then the
incremental/concurrent, generational promotion/oldification and relocating
policy/remap state machines.  After every production GC symbol has a strict
pcc-Python owner, prove no production GC C object is linked, run the full
five-GC semantic/fixed-point matrix once, and record long-running
RSS/fragmentation/pause/throughput deltas.
