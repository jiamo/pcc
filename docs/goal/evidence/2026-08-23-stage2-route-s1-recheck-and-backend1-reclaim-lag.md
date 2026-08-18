# Stage2 route: current-source S1/S2 re-check, stale-claim repair, and one new correctness blocker

Date: 2026-08-23

Rows: `PERF-P0-PCC1-BOOTSTRAP-BEATS-HOST` (route), `ARCH-P0-PROVENANCE-GRANULE-MAP`
(prerequisite), `GC-P0-BACKEND1-RESURRECTED-RECLAIM-LAG` (new)

Status: correctness boundary confirmed on current source; no timing,
measurement or optimization claim.

## Why this route

`PERF-P0-PCC1-BOOTSTRAP-BEATS-HOST` has seven prerequisites and is not
directly actionable. Its own routed investigation
`docs/investigations/pcc1-stage2-emit-throughput-and-memory.md` closes Update
No.53 with the emit-local route exhausted at every measured level — text
lifecycle, parse-local regex/interning, planner/stackmap/cursor/concurrency
shapes, and the sidecar wire — and names the remaining measured owners as the
uniform pcc1 execution penalty and the 66.2% GC/refcount leaf tax. In the
frozen emit-worker profile the largest single leaf is
`_pcc_gc_managed_pointer_find_slot` at 11.3% of the worker. That is the
subject of `ARCH-P0-PROVENANCE-GRANULE-MAP`, which is the one `IN_PROGRESS`
prerequisite, so the stage2 route runs through it.

## Current-source S1/S2 correctness is green (and the receipts needed redoing)

The granule row's gate receipts are hash-bound to allocator sha `76a996a3`.
Current source is `baf99ebe`, so those receipts do not describe the tree. Re-run
as specified:

```text
gtimeout 630s zsh -o pipefail -c "gtimeout 600s env -u LC_ALL uv run pytest \
  -vv -x -n0 --tb=short tests/python/test_gc_granule_map.py \
  tests/python/test_runtime_pointer_provenance.py \
  tests/python/test_runtime_layout_contract.py \
  2>&1 | tee build/granule-s1-current-all.log"

13 passed in 154.57s (0:02:34)
```

That covers the real-pthread live-publication, object-lifecycle race,
table-grow race, GC3-minor forwarded-source staleness and GC4 fallback-tail
retirement cases, C and pcc-Python exact provenance under GC0..4, and the
C/port layout contract.

## The row's "zero production callers" statement was stale

The granule row still said S2 had no production callers. It does now. The
strict port routes provenance through the granule fast positive:

* `py_gc_backend.py` `pcc_gc_pointer_is_managed` takes a lock-free exact
  positive from `pcc_gc_granule_is_object_start` before touching the graph
  lock, and every other outcome runs the complete historical chain;
* `pcc_gc_pointer_register` skips the exact-set insert when the granule
  publish succeeds;
* `pcc_gc_pointer_unregister` retires the marker and still removes an exact
  key when the slot was not live.

So the remaining granule work is S3 measurement, not S2 implementation. The
row was corrected in place. Note for whoever runs S3: the 21.26% / 18.98%
index-machinery baselines predate this activation and must be re-measured on
current source before any A/B bar is claimed against them.

## New blocker: backend 1 reclaims a resurrected object one collect late

The granule row lists "five-GC finalizer/resurrection/weakref/trashcan focused
gates remain green" as a pre-S2-stage prerequisite. It is not green:

```text
gtimeout 600s env -u LC_ALL PCC_GC_BACKEND=$b uv run pytest -q -x -n0 --tb=line \
  tests/python/test_gc_finalizer_corner.py tests/python/test_gc_g2_finalizers.py \
  tests/python/test_gc_g3_weakref.py tests/python/test_gc_resurrection.py \
  tests/python/test_gc_trashcan.py

backend 0   43 passed in 129.06s
backend 1    1 failed, 27 passed in 60.56s
backend 2   43 passed in  92.02s
backend 3   43 passed in  89.11s
backend 4   43 passed in  85.23s
```

`test_resurrection_does_not_block_other_cleanup` gets reclaim count `0` where
`>=1` is required. Four probes bound it: nothing leaks (the next collect
returns the same total), explicit collect is not generally broken (50-object
rounds reclaim every round), finalizers in general are not affected (a
non-resurrecting `__del__` self-cycle reclaims on the first collect), and two
drain collects before the drop remove the difference entirely. The public
entry point documents itself as a full-heap boundary, so a second required
call is a contract deviation rather than legitimate incremental pacing.

Recorded in
`docs/investigations/gc-backend1-resurrected-object-reclaim-lags-one-collect.md`
with one hypothesis and a named discriminating experiment, no source change.
Tracked as `GC-P0-BACKEND1-RESURRECTED-RECLAIM-LAG` and added to the granule
row's `depends_on`.

## Board and doc gates

```text
gtimeout 60s env -u LC_ALL uv run python scripts/goal_state.py validate
  OK: 386 tasks validated
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/test_goal_state.py
  16 passed in 0.06s
scripts/regen_investigations_index.py
  wrote docs/investigations/INDEX.md — 468 entries
scripts/goal_state.py render-startup --write docs/current-goal-state.md
  OK: docs/current-goal-state.md (was reported stale before this)
```

`tests/test_goal_startup_docs.py::test_historical_ledgers_are_preserved_and_startup_docs_are_compact`
remains RED and is pre-existing: `docs/current-goal-state.md` is 32,791 bytes
at HEAD against a 20,000-byte bound. Regeneration reduced it to 32,655 bytes;
closing the bound is the separately tracked governance row's exit criterion,
not this slice.

## Nonclaims

No stage1 or stage2 timing was captured. No module98 A/B, no re-measured
index-machinery share, no pcc1 build, no CPython control, no fixed point and
no five-GC matrix. Nothing here claims the granule S2 activation is faster —
only that its correctness gates are green on current source and that one
five-GC prerequisite is not.
