# Concurrent commit-race probe: two review findings fixed — 2026-08-26

Two P1 findings against
`test_concurrent_tracer_races_callback_commit_paths` (renamed to
`test_concurrent_tracer_races_dict_hash_commit_paths`), both fixed. The
probe is in `tests/python/test_gc_threading_substrate.py`.

## Finding 1: sticky window let non-racing schedules pass

`mutation_active` wrapped the whole mutation loop and stayed 1 while the
mutator spun waiting for tracer progress after its last mutation, so a
worker step landing entirely after all mutations counted as proven
overlap. Fixed on three levels:

1. **Per-op epoch bracket.** `mutation_active` is gone. A monotonically
   increasing `op_seq` is bumped immediately before every
   `py_dict_set` / `py_dict_del`; the worker brackets each
   `pcc_gc_step` between two reads of `op_seq`. Only steps whose bracket
   changed count as `steps_spanning_op` — a step landing during idle
   spinning counts nothing, because there are no idle-spin periods left
   (see 3).
2. **Callback-interior intersection required.** The allocating
   `__hash__` callback samples the worker's `tracer_in_step` flag for
   its whole duration; `overlap_hits > 0` is REQUIRED (exit 31
   otherwise), proving the tracer was inside `pcc_gc_step` while an op
   was inside the rooted hash-restart path — not merely inside the
   enclosing call.
3. **Churn-until-proven.** After the minimum rounds the mutator keeps
   replacing the entry (each with its own epoch bump) until the race is
   proven; delete and refill run afterwards inside the still-live
   tracer. The loop structure makes a no-mutation-in-flight window
   unreachable by construction.

Backend 0 still requires spanning steps plus intersection but not GC
progress (its step has none to make); backends 1-3 additionally require
non-zero progress inside spanning steps.

## Finding 2: overclaimed closure of the task boundary

The docstring claimed to close the whole concurrency boundary of
`GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT` while excluding backend 4,
`py_dict_update`, all set operations, and equality callbacks. Renamed
and rewritten: the probe now claims exactly *dict hash-callback
insert/replace/delete/refill under a live concurrent tracer on backends
0-3*. The remaining surfaces stay in that task row's open boundary;
backend 4 remains owned by
`GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED`.

The sibling overlap probe (`test_concurrent_tracer_overlaps_container_
mutation`) shares the sticky-window defect; the finding is recorded on
its own row (`GC-P1-CONCURRENT-TRACER-PROBE-MUST-PROVE-OVERLAP`) and
the fix is owed there, not silently done here.

## Two earlier failures were my own probe bugs, measured

Before the redesign, two red runs were accounting errors, not runtime
defects: a leaked `py_dict_get` new-ref (rc 29, refcount 3 instead of
2) and a wrong dict-only expectation that ignored the get ref (rc 30).
Both re-measured green after the fix; no runtime change was involved.

## Gates

```text
dict_hash_commit_paths, 8 arms (c+pcc_python x backends 0-3)  8 passed
backend 2 stability, 3 consecutive full runs                  3 x 2 passed
test_python_dict_methods_parity + test_python_set_methods_parity  23 passed
substrate -k collect_during (regression neighbors)            50 passed
tests/python/test_bootstrap_gate_baseline.py                   2 passed
fallback baselines (both files)                               40 passed in 537.27s
```

Envelope note: the fallback ratchet previously recorded 182.73s; the
clean serialized run took 537.27s. An earlier attempt was killed by a
400s watchdog without producing a summary (not evidence, discarded).
The current-source envelope should be re-recorded before anyone relies
on a sub-600s watchdog for that gate.

## Nonclaims

- Delete/refill run inside the live tracer but the per-op race proof
  (epoch bracket + callback intersection) is established on the churn
  path; those two ops are verified by committed-state assertions, not
  by per-op attribution.
- Victims' `__del__` may fire on the tracer thread mid-race; committed-
  state observation FROM a concurrently-running finalizer is not
  asserted here.
- Backend 4 is unprobed by design; its concurrent-tracer obligations
  are unresolved in their own row.

## Update (same day): remaining dict/set surfaces covered

Two sibling probes extend the epoch-bracket race proof to the rest of the
row's exercisable boundary:

- `test_concurrent_tracer_races_set_add_remove` -- set add/remove commit
  cycles through the allocating hash callback (identity fast path; see the
  instance-`__eq__` note below).  Post-drain: single member, exact
  sole-ownership refcounts, every displaced element finalized exactly once.
- `test_concurrent_tracer_races_dict_update_walk` -- repeated
  `py_dict_update` walks under the tracer (the snapshot-before-callback
  discipline in py_dict.c is what is being raced); identity-stable keys,
  value churn gives exact finalization accounting, src+dst shared-binding
  refcounts asserted exactly.

Writing the equality probe surfaced a runtime gap instead of a pass:
`py_obj_eq` never dispatches user-instance `__eq__`, so container keys
collapse by identity only and the probe's required eq-callback
intersection was unreachable (first run ground to its iteration bound,
subprocess timeout at 120s).  Confirmed with a minimal repro
(`eq_calls=0 len=2` where CPython gives `len==1`) and filed as
`SEM-P1-INSTANCE-EQ-CONTAINER-KEYS` with investigation
`docs/investigations/py-instance-eq-ignored-in-container-keys.md`; the
ready-made concurrent regression ships with that row when the fix lands.

```text
set_add_remove + update_walk, 16 arms        16 passed
backend 2 stability x3                        3 x 4 passed
combined races_ gate                          24 passed
```

## Update (same day, later): the sibling overlap probe fixed

`test_concurrent_tracer_overlaps_container_mutation` received the same
epoch-bracket treatment its row's finding demanded: `mutation_active`
replaced by per-op `op_seq` bumps, worker counts only steps whose bracket
changed (`steps_spanning_op` / `progress_spanning_op`), and the mutator
keeps churning past ROUNDS until the race is proven so no idle window
exists.

Two intermediate failures were mine and are recorded because the second
one produced a REAL deadlock sample: (a) my multi-hunk edit dropped the
value-creation line (`v == NULL` with no definition) - compile red;
(b) a follow-up repair silently dropped the epoch bump and
`rounds_done++`, so the mutator inserted forever without counting rounds
and the bound path ran while the CMS worker held stop-the-world - the
main thread sat in `pthread_join` without polling safepoints, exactly the
invisible-to-STW deadlock the probe's own comment warns about. `sample`
showed main in `_pthread_join` + worker in `pcc_stop_the_world`. The
bound path now uses the same safepoint-polling shutdown as every other
exit; both defects re-measured green after the fixes.

```text
overlap probe, backends 0-3, 8 arms         8 passed
overlap probe, backend 2 stability x3        3 x 2 passed
overlap probe, backend 4 arms this run       2 passed (flake remains
                                             historical 1-in-8, row open)
```

## Update (same day, latest): backend-4 flake attributed to the mutator thread

The overlap probe now records which thread ran each finalization
(`last_fin_thread` via `pcc_current_thread_id()` inside `probe_del`,
printed only on the premature-free failure path). Backend-4 arm looped
until reproduction - first run reproduced:

```text
seen=122, displaced=99: the surviving value was freed too
last_fin_thread=1 main_thread=1
```

Two facts, both new:

1. The survivor's `__del__` ran on the MUTATOR thread (thread 1), not the
   tracer worker. The concurrency dependence is real but indirect: a
   worker's tracing steps change WHEN mutator-side collection triggers,
   and some mutator-side path (allocation-poll collection or relocation
   drain inside `py_dict_set`) then frees a value still stored in the
   committed dict.
2. The delta is 23, not 1: two dozen values were finalized beyond the
   displaced count, so either multiple live values were freed or
   displacement accounting undercounts under relocation - consistent with
   the earlier observation that teardown shape moved the count.

Row `GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED` owns the narrowed next
step: identify the mutator-side free path (watchpoint or per-value
generation tags) before any exact-obligation claim for backend 4.

## CORRECTION (same day, final): entry-loss lead retracted

The "backend-4 flake attributed to the mutator thread" update above
remains valid (thread attribution came from the correctly-written overlap
probe). But the follow-up deterministic "entry loss" diagnostic
(298/300, zombie dict, forwarding-vs-copy race) was RETRACTED: my
attr4/attr5 probes held a top-of-round container pointer across
relocation boundaries. Re-run with reload-inside-call semantics
(`attr6.c`, identical to the passing probes): **0 losses in 5 x 300
rounds** with the worker stepping continuously.

The zombie-dict dump measured relocation itself, not the container — the
exact convincing-false-finding pattern the substrate probe docstrings
warn about. Investigation
`docs/investigations/gc-backend4-concurrent-entry-loss.md` carries the
full correction trail; the historic overlap flake returns to UNEXPLAINED,
anchored by the mutator-thread finalization datum, and the BACKEND4 row's
boundary lists the two candidate next steps (per-value generation tags or
finalizer backtrace capture).
