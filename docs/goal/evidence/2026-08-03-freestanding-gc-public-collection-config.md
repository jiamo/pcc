# Freestanding GC public collection/config evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns GC environment parsing,
one-time backend configuration, CMS worker-start accounting, and the four
public tracing-collection entrypoints.  The managed `py_gc_backend.py` keeps
only exact extern aliases for config initialization and CMS-start accounting;
it no longer defines any of the seven migrated symbols.

This proves the public config/collection boundary for
`LIBC-P2-FREESTANDING-GC` in no-libpython pcc-native mode.  Incremental and
concurrent scheduling, generational promotion/oldification, relocating
policy/remap, the final five-GC fixed point, and long-run metrics remain open,
so the task remains `DONE_WEAK`.

## Ownership and preserved semantics

`freestanding_gc_public_collection.py` exports exactly:

```text
pcc_gc_config_parse_env_i32
pcc_gc_config_ensure
pcc_gc_maybe_start_cms_worker
pcc_gc_has_tracing_sweep
pcc_gc_collect_tracing
pcc_gc_begin_explicit_tracing_collect
pcc_gc_end_explicit_tracing_collect
```

LLVM and self emission produce the same exact finite raw closure.  The current
production archive
`ceaa8c1231cee48fd68d5c9f-pcc-py/libpy_runtime_pcc_py.a` reports each export
exactly once from `freestanding_gc_public_collection.o`.

The source contract preserves all existing environment names, defaults and
clamps, initializes the selected backend before CMS-start accounting, admits
tracing collection only for backends 1..4, keeps stop-the-world before bounded
sweep and resume afterward, and publishes explicit-collection-active before
requesting a tracing cycle.

## Focused gates

```text
tests/python/test_freestanding_gc_public_collection.py
  5 passed in 58.49s

tests/python/gc/test_gc_backend_config_fastpath.py
  7 passed in 0.46s

tests/python/test_gc_backend_incremental.py + test_gc_backend_concurrent.py
  14 passed in 69.34s

five-backend object lifetime + finalizer cycle/resurrection + weakref callback/finalizer
  30 passed in 4.61s

tests/python/test_gc_update_referents.py
  31 passed in 7.82s

tests/python/test_gc_backend4_production.py
  128 passed in 17.52s
```

The TDD pre-change node failed only because the strict source did not exist:
`1 failed in 0.10s` with `FileNotFoundError`.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-public-collection-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-public-collection-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=32598 \
  output=build/freestanding-gc-public-collection-stage1/pcc1
```

The stage profile records 31.383 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That pcc1
compiled the real strict module with `--ir-scaffold=on --backend self
--python-libpython off --python-library`; clang accepted the IR, all seven
exports are definitions, and no `call` or `invoke` targets `py_cpy_*`.

## Scoped hashes

```text
124ce6896fbf40e4b3cb7eace7593d4762901272d9f2b16799ddf226edbc875e  pcc/py_runtime/py/freestanding_gc_public_collection.py
aeab32596147375edd7093dcb324a1ec4ed6c9cb51f4186010570c8dac585070  pcc/py_runtime/py/py_gc_backend.py
3cb06238af425adcc4c4ef9d1415629441f028d2dbb04a28c41b0382465a69cc  pcc/py_frontend/codegen/runtime_abi.py
81819cc69a5824af95417c7e9633f5416ca8c070ad750b08effff8f67aad4c60  pcc/py_runtime/Makefile
cbe1b9903ae7571526dc93fab4c4ca64f77f4e3a40ae2fc7716cdb5d030d5b13  tests/python/test_freestanding_gc_public_collection.py
3a0168477f0976a10dae3162214845358ebfe043b93111cac0f49d085ce9db26  tests/python/gc/test_gc_backend_config_fastpath.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move incremental/concurrent scheduling next, then generational
promotion/oldification and relocating policy/remap.  After every production GC
symbol has a strict pcc-Python owner, prove no production GC C object is linked,
run the full five-GC semantic/fixed-point matrix once, and record long-running
RSS/fragmentation/pause/throughput deltas.
