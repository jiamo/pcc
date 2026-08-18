# GC4 A3b relocation-reseed bounded relocation aggregate

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b holder sub-boundary confirmed; parent task remains
`IN_PROGRESS`.

## Claim boundary

Relocation-epoch reseed in the C and strict pcc-Python roots now computes its
candidate count plus total/small/medium byte aggregates in batches of at most
16 relocation nodes per graph-lock tenure. An unlink-aware raw cursor and
graph-locked relocation-list revision restart every aggregate when selection,
relocation-copy detach, object freeing or full reset changes the list between
batches.

The stable aggregate finishes while retaining the graph lock and immediately
enters the existing page rebuild/commit, so no candidate mutation can occur
between the completed relocation snapshot and that still-unbounded phase.
Non-final aggregate batches unlock, enter the phase-2 deterministic probe,
safepoint, then reacquire.

This closes the relocation aggregate only. Reseed's page-list rebuild/commit
still walks all pages and repeatedly searches the relocation list under one
graph-lock tenure. No page pointer is carried across unlock by this slice.

## Genuine RED

`test_relocation_reseed_aggregate_is_bounded_and_restartable` was added before
implementation and failed on the absent relocation cursor:

```text
1 failed in 0.33s
AssertionError: assert 'pcc_gc_backend4_reseed_relocation_cursor' in ...
```

## Implementation

- C and strict state add one relocation cursor and revision.
- Candidate add, relocation-copy detach, object-free removal and reset advance
  the cursor before recycling a matching node and increment the revision on
  every list mutation.
- Reseed resets all six relocation aggregates and restarts from the current
  head if the revision changes after a batch.
- The existing diagnostic pause is now a phase bitmask: bit 1 covers count /
  prepare and bit 2 covers relocation aggregate. Defaults remain inactive.

## Focused evidence

All pytest commands stopped at the first failure.

1. A deterministic C/strict pthread probe selects 24 distinct medium pages,
   pauses after aggregate node 16, concurrently performs full reset/unlink/
   recycle, then resumes. Both roots restart to zero without UAF and recover
   all 24 candidates/pages and 1440000 bytes on reselection.

2. The final packet includes that interleaving, prior count and plan-window
   interleavings, four-thread reset/reseed, exact strict raw ownership, and
   real C/strict relocation-copy target-phase parity:

   ```text
   gtimeout 120s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend4_production.py::test_relocation_reseed_aggregate_is_bounded_and_restartable tests/python/test_freestanding_gc_relocation_selector.py::test_relocation_selector_has_one_strict_source_owner tests/python/test_freestanding_gc_relocation_copy.py::test_relocation_copy_has_one_strict_source_owner tests/python/test_gc_backend4_production.py::test_c_reseed_aggregate_cursor_survives_concurrent_full_reset tests/python/test_gc_backend4_production.py::test_strict_reseed_aggregate_cursor_survives_concurrent_full_reset tests/python/test_gc_backend4_production.py::test_c_reseed_count_cursor_survives_concurrent_full_reset tests/python/test_gc_backend4_production.py::test_strict_reseed_count_cursor_survives_concurrent_full_reset tests/python/test_gc_backend4_production.py::test_c_reseed_forces_plan_growth_and_allocation_failure tests/python/test_gc_backend4_production.py::test_strict_reseed_forces_plan_growth_and_allocation_failure tests/python/test_gc_backend4_production.py::test_c_concurrent_reset_reseed_revalidates_prepared_plan tests/python/test_gc_backend4_production.py::test_strict_concurrent_reset_reseed_revalidates_prepared_plan tests/python/test_gc_backend_relocating.py::test_colored_relocating_targets_wait_for_phase_reset tests/python/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_targets_wait_for_phase_reset 2>&1 | tee build/gc4-a3b-reseed-aggregate-final.log'
   13 passed in 9.46s
   ```

3. All four affected strict modules compiled under
   `--backend self --python-libpython=off --ir-scaffold=on --python-library`;
   the receipt is `build/gc4-a3b-reseed-aggregate-closures-final.log`. Python
   syntax, C syntax with `PCC_WITH_THREADS=0/1`, and `git diff --check` passed.

## Frozen identities

```text
a49cc5525fb9e0b000f38637240e996e6f5cea9cc44989abf6668db755e14b35  pcc/py_runtime/src/py_gc_backend.c
49aa0c797c7270399dcc2411979d81187c511b5a5a801071d9e9bc0461c25bce  pcc/py_runtime/include/py_runtime.h
77b78dbca15487b14daeec350646f247170d9529698f4d6eb33c5392f8fe5c09  pcc/py_runtime/py/py_gc_backend.py
54b7bb67a66e8193554367bbcdc4ba532170e65f68bab0c14f787c9e1ebd2226  pcc/py_runtime/py/freestanding_gc_state.py
51c3ec2bbb902adef051ef42a0f01faab865697f67b0392a43c28262134bfe63  pcc/py_runtime/py/freestanding_gc_relocation_selector.py
7a195e55ef2f5123363ab04efd092e0cccf6705ec141f5b1b37c0b35e8fb807b  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
2bcc869db9cc2fb41162ae6218132b09b1f39b7ce1d929e2e07ffee13a808615  pcc/py_frontend/codegen/runtime_abi.py
82648206adab7afa9ad06dd125fcf23e04c92f9f0dfb0c7392081a60e8129d39  tests/python/test_gc_backend4_production.py
7e753701e6576bc6867f4c829bb6ab9887dab0dabfd93402e6747b6e28a8b2b0  build/gc4-a3b-reseed-aggregate-final.log
6c81e3deb6a23a51b427b43a3c5b96db2b88a6b5ef7b08784bf82150ab837c15  build/gc4-a3b-reseed-aggregate-closures-final.log
```

## Next boundary

Bound or split the remaining page rebuild/commit scan. A raw page pointer may
not survive graph unlock without an explicit page lifetime/lease mechanism;
cursor and revision protect list nodes, not detached page storage. Preserve
the proven plan growth, OOM, count and aggregate invalidation behavior. Do not
connect A3c yet.
