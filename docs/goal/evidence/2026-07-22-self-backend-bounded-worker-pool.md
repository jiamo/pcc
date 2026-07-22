# Self-backend bounded worker-pool closure

## Claim

Direct self-backend emission now defaults to a conservative two-process pool
and reuses each compiled worker across a batch of IR/object jobs. Explicit
`PCC_SELF_BACKEND_JOBS` and bootstrap-specific throughput settings remain
authoritative. This proves bounded process starts, deterministic publication,
failure propagation, cache behavior, current-source pcc1 NumPy L5 execution,
and complete-suite compatibility; it does not claim a like-for-like wall-time
speedup for every workload.

## Change

- Added a versioned batch-manifest protocol and compiled-stage batch-worker
  entrypoint.
- Partitioned cache misses across the selected pool width, with one persistent
  worker invocation per partition instead of one compiler process per object.
- Kept final object collection in original input order and preserved the
  single-item protocol for compatibility.
- Made the unset-environment direct default two workers while retaining
  explicit overrides.

## Evidence

- focused IR pipeline and bootstrap shim: 165 passed in 310.42s.
- current-source pcc1 NumPy L5 under no-libpython/self and GC0..4: 1 passed in
  117.19s.
- complete non-integration suite: 9403 passed, 114 skipped, 1 warning in
  706.50s (11m46s).
- complete integration suite: 4551 passed, 12 skipped in 1316.67s (21m56s).

The original direct NumPy path launched 114 short-lived compiler processes in
batches of twelve. The new default launches at most two batch workers and
processes multiple objects per invocation. All gates used explicit watchdogs;
no compiler case or suite item was removed, skipped, or weakened.

The integration run separately exposed an outer-suite resource issue: after
GC0 warmed the cache, the bootstrap lease admitted three full GC chains at
once and combined RSS peaked at 18.2 GiB. That is tracked independently as
`TEST-P0-FULL-GC-MEMORY-BUDGET`; it does not reopen the direct emitter-pool
claim closed here.
