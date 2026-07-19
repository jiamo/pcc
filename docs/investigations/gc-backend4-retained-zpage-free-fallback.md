# GC4 retained zpage free fallback and malloc-origin fast path

Date: 2026-06-15
Mode: strict no-libpython self backend, Darwin arm64
Status: resolved for bootstrap correctness and short-workload performance; RSS
span-retention remains an explicit open cost.

## Symptom

GC backend #4 was correct only with a broad span-retention fallback, but that
fallback made some bootstrap workers pathologically slow. A direct attempt to
physically release overflow zpage spans looked attractive for RSS, but it
crashed the stage worker during teardown:

```text
pcc_gc_free_object_memory -> py_instance_dealloc -> ... ->
pcc_gc_store_root -> _pcc_py_module_fini_pcc_cli_bootstrap
malloc: pointer being freed was not allocated
```

The crash proved that some stale or delayed references can outlive owner-index
membership. Treating those addresses as ordinary malloc blocks is not safe.

## Hypotheses

1. **Physical zpage span release is safe after owner-index removal.**
   Denied. The worker abort above shows the runtime can later see an address
   from a retired zpage span without its old owner-index entry.
2. **A retained-page list preserves correctness but creates a linear hot path.**
   Confirmed. Retaining pages prevented the abort, but a bootstrap worker timed
   out at 600s with samples dominated by `_backend4_zpage_list_owns_addr`.
3. **Normal malloc-origin objects should skip retained-span scans entirely.**
   Confirmed. Adding a GC4 `PY_FLAG_GC_MALLOC_ALLOC` bit lets leaf/malloc
   objects bypass the retained zpage address lookup while preserving the
   fallback for suspicious unflagged addresses.

## Fix

- GC4 zpages now have three states:
  - active pages
  - bounded reusable free-cache pages
  - retained, non-reusable pages whose spans are still recognized
- The reusable cache remains bounded: small pages keep at most 8 cached pages,
  medium pages keep at most 4, and large/overflow pages retire to the retained
  list instead of growing the reusable search surface.
- `pcc_gc_note_object_freeing()` and `pcc_gc_free_object_memory()` still recover
  zpage ownership by address when an object lost its zpage flag and owner-index
  entry, but only when the object is not explicitly marked malloc-origin.
- `pcc_gc_alloc()` marks GC4 malloc fallback objects with
  `PY_FLAG_GC_MALLOC_ALLOC`, mirrored in the pcc-Python runtime.
- `pcc_gc_release()` now returns immediately for `NULL` and tagged ints before
  querying the selected backend. This is a common hot-path optimization and
  does not change heap-object relocation, finalizer, weakref, or root handling.

## Evidence

Focused correctness and shape gates:

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_obj.py \
  tests/python/gc/test_gc_backend_config_fastpath.py

gtimeout 300s env -u LC_ALL uv run pytest \
  tests/python/gc/test_gc_backend_config_fastpath.py \
  tests/python/test_gc_backend4_production.py::test_backend4_zpage_free_fallback_checks_retained_span_address \
  tests/python/test_gc_backend4_production.py::test_backend4_skips_zpage_and_graph_for_leaf_objects \
  -q -n0
```

Result:

```text
9 passed in 12.63s
```

GC4 production contract:

```bash
gtimeout 300s env -u LC_ALL PCC_GC_BACKEND=4 uv run pytest \
  tests/python/gc_production_contract -q -n0
```

Result:

```text
130 passed in 51.08s
```

Full all-five bootstrap matrix after the final hot-path change:

```bash
gtimeout 1200s env -u LC_ALL PCC_BOOTSTRAP_FULL_REBUILD=1 uv run pytest \
  -q -n0 -s \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py
```

Result:

```text
5 passed in 297.55s (0:04:57)
Bootstrap OK under PCC_GC_BACKEND=0..4: pcc2 and pcc3 are byte-identical.
```

Final 9-run GC advantage matrix:

```bash
gtimeout 900s env -u LC_ALL uv run python \
  benchmarks/run_gc_advantage_matrix.py \
  --outdir /tmp/pcc-gc-advantage-matrix-20260615-final-v3 \
  --reps 9
```

All five target backends still won their encoded metrics. GC4's encoded win is
pause behavior: `gc4_colored_low_total_pause` had median `pause_sum_us=91`,
0.137x GC0 and 0.105x GC3 on that metric.

Python scenario rerun after the tagged-int release fast path:

```text
typed_loop self GC0: 0.423x CPython
typed_loop LLVM GC0: 0.202x CPython
dict_heavy self GC0/GC3/GC4: 0.638x / 0.940x / 0.915x CPython
dict_heavy LLVM GC0/GC3/GC4: 0.623x / 0.945x / 0.962x CPython
closure_heavy self/LLVM GC0: 0.161x / 0.143x CPython
```

## Current boundary

The RSS/heap tax is partially optimizable, not fully removable under the
current proof:

- Safe and landed: bounded reusable cache, retained-list lookup avoided for
  known malloc-origin objects, tagged-int release avoids backend queries.
- Not safe today: physically returning retained zpage spans to libc. The direct
  experiment crashed under bootstrap teardown. Removing this cost requires a
  stronger remap/epoch proof that no stale SSA/root/trashcan/borrowed pointer
  can later reach the old span, or a different quarantined-span design with a
  proven reclamation point.

Do not describe backend #4 as a throughput or RSS winner from this slice. Its
current measured advantage case is low total pause time on sparse explicit
collections, while RSS/heap pressure remains an honest open research cost.
