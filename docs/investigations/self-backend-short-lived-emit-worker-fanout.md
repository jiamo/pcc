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

## Cold-cache invalidation of the first pool design

A later current-source GC0 gate forced native object-cache misses. Capping the
pool at two processes did not cap memory: each long-lived compiled pcc1 worker
processed an unbounded share of the object list and grew to about 10 GiB;
aggregate related RSS reached 22.8 GiB before a deliberate interrupt. Running
eight shorter workers had previously reached 25.3 GiB, so concurrency-only
tuning merely moved retained compiler heap between processes.

The revised design keeps two-process concurrency but recycles a process after
at most four objects. For 151 misses this requires about 38 process starts,
still far fewer than the old one-process-per-object protocol, while giving
compiler state a hard OS reclamation boundary. The cold fixed-point gate must
prove the resulting peak before this investigation can return to resolved.

## Full-GC aggregate frontend concurrency

A later complete integration run exposed a separate composition failure: the
two-chain GC lease still assigned four frontend workers to each concurrently
active pcc2-to-pcc3 chain.  Aggregate descendant RSS reached 16.61 GiB and the
run was deliberately terminated at the safety boundary.  The scheduler now
shares one four-worker frontend budget across active GC chains (four for one,
two each for two), while retaining the explicit per-chain override and
restoring four workers for the last chain.  Focused scheduling tests pass, and
the forced-rebuild five-GC matrix passes all five backends in 1306.29 seconds
with a sampled 13.13 GiB peak.  Complete-suite validation remains open.

## Update 2026-09-04 — current guarded Stage2 closes the reopened safety boundary

The later memory circuit breaker and measured admission scheduler replace the
unsafe assumption that a fixed process count bounds memory. Each compiled
worker remains short-lived; lane admission uses observed process-tree RSS and
per-item floors, stops admitting under pressure, and can suspend/resume the
youngest worker. An external 8 GiB process-tree guard remains authoritative.

Frozen current-source Stage2 completed rc0 in 1349.675s at
7,812,333,568 bytes peak and produced a runnable libSystem-only pcc2. The 224
misses were split into one serial, six paired-oversized, eight heavy, sixteen
medium and 193 small workers; pressure produced 2,161 denied admission polls
and three suspend/resume cycles rather than unbounded fanout. No sampler table
retry or child leak occurred.

Current cache/identity focused gates pass 17 cases and bootstrap resource/cache
gates pass seven. The retained phase-reuse receipt remains byte-identical and
93.2% faster on an equivalent warm invocation. Full details are in
`docs/goal/evidence/PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE/002-current-cache-contract-and-safety-reclosure.md`.

## Report

The original fixed-width pool was insufficient because compiler heaps grow by
module; the resolved design combines short-lived workers, lane-specific
measured admission, live RSS feedback and an external hard ceiling. The result
is memory safety, not acceptable cold performance: the 1350-second Stage2 is
now owned by the native data-plane emit task. Fresh final-source Stage3 and
GC1--4 remain deliberately downstream rather than being inferred here.
