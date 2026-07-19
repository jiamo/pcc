# Five-GC bootstrap object cache and scheduling evidence

Date: 2026-07-17

Task: `M0-P0-FIVE-GC-BOOTSTRAP-MATRIX-SCHEDULING`

## Outcome

The five full bootstrap items are independently visible to xdist, while an
in-test resource lease serializes only the cold GC0 warmer and bounds later
concurrency.  A content-addressed, SHA-256-verified native-object cache reuses
deterministic self-backend objects without skipping compiler frontend work.
Compiled emitter workers now reclaim their own heap by process exit; the
parent no longer performs a redundant GC3 full collection.

## Gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_pcc_bootstrap_full.py \
  tests/python/test_py_multi_file_bootstrap_shim.py \
  -k 'parallel_slots or matrix_plan or active_gc or run_order or object_cache'
9 passed, 97 deselected in 0.69s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py \
  tests/python/test_ir_py_fallback_baseline.py
25 passed in 250.34s (0:04:10)

gtimeout 1800s env -u LC_ALL uv run pytest -q \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py
5 passed in 1500.11s (0:25:00)

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_self_native_emitter_collects_incrementally \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_self_native_emitter_uses_short_lived_compiled_stage_workers \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_self_native_emitter_skips_compiled_workers_for_cache_hits
3 passed in 0.27s

gtimeout 700s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py
1 passed in 533.59s (0:08:53)
```

The last gate is the final-source focused validation after the parent-collect
optimization.  Its source identity was
`aacec3d2b7e5a6c700e0716d0821bc85b37fa669a8210f272c1a3401a4ffbc88`.

## Performance evidence

```text
GC3 stage3 before: total 553.387s; native phase 400.663s
GC3 stage3 after:  total 131.944s; native phase   7.688s
change:             total -76.2%; native phase -98.1%

after stage2 (cold): 0 hits / 196 misses; collect_skipped=1
after stage3 (warm): 196 hits / 0 misses; collect_skipped=1
```

The GC3-only outer wall time includes shared-stage1 rebuilding and temporary
contention from an unrelated eight-worker CPU search.  Profile phase times are
the appropriate evidence for the removed compiler cost.

## Claim boundary

This proves five-GC scheduling and fixed-point correctness on the local
AArch64 Darwin self backend, plus the measured cache/collection speedup on
this machine.  It does not claim cross-machine performance, GPU execution,
or a universal compiler speedup.  No full GCC suite was run.
