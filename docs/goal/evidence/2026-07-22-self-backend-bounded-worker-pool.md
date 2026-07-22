# Self-backend bounded worker-pool closure

## Superseded claim and reopened boundary

The original evidence proved two-process concurrency and fewer starts, but a
later cold-source GC0 run invalidated the unlimited worker-lifetime claim. Two
persistent compiled emitters each accumulated about 10 GiB and related RSS
reached 22.8 GiB before a deliberate interrupt. The replacement recycles a
compiled emitter after at most four objects, retaining two-process concurrency
while giving compiler heap an OS reclamation boundary.

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
- current-source cold GC0 fixed point with recycling: 1 passed in 532.73s,
  sampled peak 8.93 GiB;
- forced-rebuild current-source five-GC matrix: 5 passed in 1306.29s, sampled
  aggregate peak 13.13 GiB after the outer frontend budget repair;
- final complete integration suite: 4551 passed, 12 skipped in 669.70s;
- final complete non-integration suite: 9503 passed, 28 skipped, 1 warning in
  970.63s, with a transient sampled 15.21 GiB peak that fell to 4.17 GiB when
  the heavy pcc1 child exited.

The original direct NumPy path launched 114 short-lived compiler processes in
batches of twelve. The new default launches at most two batch workers and
processes multiple objects per invocation. All gates used explicit watchdogs;
no compiler case or suite item was removed, skipped, or weakened.

The integration run separately exposed an outer-suite resource issue: after
GC0 warmed the cache, the bootstrap lease admitted three full GC chains at
once and combined RSS peaked at 18.2 GiB. That is tracked independently as
`TEST-P0-FULL-GC-MEMORY-BUDGET`; it does not reopen the direct emitter-pool
claim closed here.

The four-object process lifetime, deterministic publication/cache/failure
tests, cold fixed point, NumPy L5 gate, forced matrix, and both complete suites
are now green. No compiler case or marker was removed or weakened.
