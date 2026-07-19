# Five-GC Advantage Workloads

Date: 2026-06-15
Host: macOS 26.5.1, Darwin 25.5.0, arm64
Mode: strict no-libpython self backend unless stated otherwise

This document is not a global collector ranking. It records one measured
advantage workload for each `PCC_GC_BACKEND=0..4`, with the exact benchmark code
and runner that reproduce the claims.

Code and tests:

- Benchmark program:
  [/Users/jiamo/my/pcc/benchmarks/python/gc_advantage_matrix.py](/Users/jiamo/my/pcc/benchmarks/python/gc_advantage_matrix.py)
- Host runner:
  [/Users/jiamo/my/pcc/benchmarks/run_gc_advantage_matrix.py](/Users/jiamo/my/pcc/benchmarks/run_gc_advantage_matrix.py)
- Matrix smoke test:
  [/Users/jiamo/my/pcc/tests/python/test_gc_advantage_matrix.py](/Users/jiamo/my/pcc/tests/python/test_gc_advantage_matrix.py)
- Hot-path guard:
  [/Users/jiamo/my/pcc/tests/python/gc/test_gc_backend_config_fastpath.py](/Users/jiamo/my/pcc/tests/python/gc/test_gc_backend_config_fastpath.py)

## Reproduce

```bash
gtimeout 900s env -u LC_ALL uv run python benchmarks/run_gc_advantage_matrix.py \
  --outdir /tmp/pcc-gc-advantage-matrix-20260615-final-v3 \
  --reps 9

gtimeout 180s env -u LC_ALL uv run pytest \
  tests/python/test_gc_advantage_matrix.py \
  tests/python/gc/test_gc_backend_config_fastpath.py \
  -q -n0
```

The runner compiles the benchmark once with:

```bash
uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  benchmarks/python/gc_advantage_matrix.py \
  -o /tmp/pcc-gc-advantage-matrix-20260615-final-v3/gc_advantage_matrix.out
```

It then runs the same binary under `PCC_GC_BACKEND=0..4`.

## Claim Summary

Lower is better for every target metric below. Values are medians from 9 runs
per backend per workload.

| Workload | Target GC | Target metric | Winner | Target value | Target / GC0 | Target / GC3 | Claim |
|---|---:|---|---:|---:|---:|---:|---|
| `gc0_refcount_steady_churn` | 0 | `elapsed_us` | 0 | 7134 | 1.000 | 0.716 | Refcount wins cycle-free allocation churn. |
| `gc1_incremental_explicit_churn` | 1 | `elapsed_us` | 1 | 7231 | 0.856 | 0.820 | Incremental tracing wins this explicit-collection churn window. |
| `gc2_cms_heap_under_high_collect_churn` | 2 | `heap_bytes` | 2 | 1376592 | 0.424 | 0.238 | CMS keeps the smallest in-use heap under high-frequency node churn. |
| `gc3_generational_high_frequency_collect` | 3 | `elapsed_us` | 3 | 48302 | 0.552 | 1.000 | Generational wins throughput when frequent collections revisit a stable live set. |
| `gc4_colored_low_total_pause` | 4 | `pause_sum_us` | 4 | 91 | 0.137 | 0.105 | Colored-relocating has the lowest total pause time on sparse explicit collections. |

## Workload Details

| Workload | Mode and parameters | Winning dimension | Median values across GC0/1/2/3/4 |
|---|---|---|---|
| `gc0_refcount_steady_churn` | `list_churn n=2048 rounds=250 inner=3 collect_every=0` | Wall time | `elapsed_us`: 7134 / 9289 / 9230 / 9962 / 12194 |
| `gc1_incremental_explicit_churn` | `node_churn n=2048 rounds=90 inner=5 collect_every=10` | Wall time with explicit collection | `elapsed_us`: 8446 / 7231 / 9210 / 8823 / 12140 |
| `gc2_cms_heap_under_high_collect_churn` | `node_churn n=2048 rounds=200 inner=8 collect_every=1` | In-use heap and RSS | `heap_mib`: 3.10 / 5.52 / 1.31 / 5.51 / 2.39; `rss_mib`: 5.72 / 8.56 / 3.45 / 8.64 / 4.52 |
| `gc3_generational_high_frequency_collect` | `node_churn n=2048 rounds=350 inner=3 collect_every=1` | Wall time under very frequent explicit collection | `elapsed_us`: 87561 / 95103 / 66038 / 48302 / 75153 |
| `gc4_colored_low_total_pause` | `node_churn n=1024 rounds=350 inner=3 collect_every=50` | Total and max pause | `pause_sum_us`: 666 / 128116 / 417 / 864 / 91; `max_pause_us`: 112 / 52 / 90 / 126 / 49 |

## Interpretation

GC #0 is still the reference backend for simple cycle-free allocation churn.
Its advantage is immediate reclamation and low bookkeeping when no tracing
collector work is useful.

GC #1 has a real explicit-collection throughput window. It is not the lowest
heap backend in that case, but it beats GC #0/#2/#3/#4 on elapsed time.

GC #2's strongest measured case here is memory pressure, not throughput. In the
high-frequency node churn workload it keeps the smallest in-use heap and RSS,
while GC #1 and GC #3 spend less wall time in some neighboring cases.

GC #3 has a strong throughput case when explicit collections happen extremely
often and the live set is stable enough for the minor/major policy to avoid
full repeated work.

GC #4's honest selling point in this slice is pause behavior, not overall
throughput or RSS. It wins total pause time and max pause on the encoded sparse
explicit-collection workload, but it still pays more heap/RSS than GC #0/#2/#3
in several churn workloads. The latest correctness-first zpage retention work
keeps old spans recognizable after owner-index removal; that avoids freeing a
zpage address through libc, but it is still an RSS/heap cost until the runtime
has a stronger remap/epoch proof.

## Common Optimizations Landed

Five semantics-preserving optimizations are reflected in this matrix:

- `pcc_gc_backend()` now returns the already-selected C backend immediately
  after configuration has been initialized. This removes a repeated
  initialization function hop from C runtime backend queries and applies to all
  five GC backends.
- `py_gc_track()` and `py_gc_untrack()` now check `pcc_threads_enabled()` before
  querying the GC backend for the threaded GC #4 special case. Non-threaded runs
  across all five backends avoid the backend query on that hot path. The change
  is mirrored in C and pcc-Python.
- `pcc_gc_release()` now returns immediately for `NULL` and tagged ints before
  querying the backend. This helped integer-heavy and dict-heavy Python
  scenarios without changing heap-object release, relocation, or finalization.
- GC #4 malloc fallback objects now carry a `PY_FLAG_GC_MALLOC_ALLOC` bit, so
  normal malloc-origin objects skip retained-zpage address scans.
- GC #4 zpage free-cache search is bounded: reusable small/medium zpages are
  cached within fixed limits, while overflow pages move to a retained,
  non-reusable list for correctness.

None of these changes disables tracing, barriers, weakrefs, finalizers, root
handling, owned-local cleanup, relocation read barriers, or bootstrap
no-libpython checks. Physical release of retained zpage spans was tested and
denied by a bootstrap teardown abort; it remains an explicit future design
problem, not a safe optimization in the current runtime.
