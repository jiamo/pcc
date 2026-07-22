# Investigation: self-backend short-lived emit worker fanout

## Status

resolved

## Problem Description

The real `pcc1 -m pip install numpy` command gate entered application
compilation with 114 independent self-backend IR/object jobs. With
`PCC_SELF_BACKEND_JOBS` unset and no outer pytest worker count, pcc selected
the host CPU count (12) and launched one complete pcc1 process per object in
batches of twelve. A process snapshot observed individual emit workers at
roughly 170--345 MiB RSS. This creates abrupt CPU and memory pressure and pays
compiler process startup 114 times.

The package C build is already capped at two workers; this is a separate
self-backend emission pool. The existing outer-parallelism budget protects
xdist runs, but direct pcc/pcc1 commands do not set that outer budget.

## Root Cause

`_self_backend_jobs()` defaults to the entire available CPU budget.
`_emit_self_objects_many_in_process()` then constructs one
`--pcc-self-backend-emit-worker` subprocess command per cache miss.
`_run_python_frontend_worker_commands()` bounds simultaneous commands but
does not reuse a worker process after one object. Concurrency is bounded only
in count, not in per-process compiler residency or total process starts.

## Design

- Keep the existing single-item worker protocol for compatibility and focused
  testing.
- Add a versioned batch manifest and compiled-stage batch worker that handles
  several IR/result/object jobs sequentially in one pcc1 process.
- Partition cache misses across at most the chosen pool width and launch one
  batch worker per partition. Keep result files and final object collection in
  original input order so output determinism and cache identity do not depend
  on scheduling.
- Default direct self-backend emission to two workers. Preserve
  `PCC_SELF_BACKEND_JOBS=N` as the authoritative explicit override; bootstrap
  already supplies its measured high-throughput value explicitly.
- Propagate the first item failure as a nonzero batch exit and do not publish
  cache results after a failed pool.

## Proposals

- No.1 Add resource-selection and batch-protocol red tests [done]
- No.2 Implement the persistent bounded batch pool [done]
- No.3 Measure current pcc1 NumPy L5 compile and validate full suites [done]

## Current Results

The final focused pipeline/bootstrap-shim cluster passes 165 cases in 310.42
seconds. A current-source pcc1 compiles and runs the pinned NumPy L5 surface
under self/no-libpython across GC0..4 in 117.19 seconds. The exact complete
non-integration suite passes 9403 cases in 706.50 seconds, and the exact
integration suite passes 4551 cases in 1316.67 seconds.

The old direct NumPy path launched 114 complete compiler processes in batches
of twelve. The replacement launches at most two batch workers by default and
reuses each across multiple objects. The evidence therefore supports bounded
process count and lower process-start overhead. It does not support a universal
wall-time speedup claim because the pre-change and post-change pcc1 runs were
not controlled like-for-like measurements.

The complete integration run also measured a distinct outer scheduling peak:
three full-GC bootstrap chains combined to 18.2 GiB RSS after the cache warmer.
That is not per-object fanout and is tracked separately in
`TEST-P0-FULL-GC-MEMORY-BUDGET`.
