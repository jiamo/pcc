# GC4 A3b reseed evacuation-node plan

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b sub-boundary confirmed; parent task remains `IN_PROGRESS`.

## Claim boundary

Backend 4 relocation-epoch reseed in the C and strict pcc-Python runtime roots
now counts the currently required evacuation-page nodes while holding the graph
lock, unlocks to prepare private nodes, reacquires the lock and revalidates the
count, then detaches and rebuilds page membership using only preallocated
nodes.  Old and surplus prepared nodes are physically released after unlock.

If preparation cannot satisfy the observed count, reseed releases its private
partial plan and returns before detaching the old evacuation-page membership.
Concurrent growth between planning and commit causes another count/prepare
iteration instead of an allocation under the lock.

This is not the lifetime-safe bounded-scan slice.  Counting and commit still
walk relocation/page/object metadata under the graph lock because those lists
contain raw non-owning pointers.  Deterministically forced plan-growth and
allocator-failure injection, GC3/callback/log holders, A3c, raw container
transactions and the collector-owned stopped-world phase remain open.

## Genuine RED

`test_relocation_reseed_prepares_evacuation_nodes_before_locked_commit` was
added before implementation.  It failed while locating the C prepare call:

```text
1 failed in 0.31s
ValueError: substring not found
```

The test now requires unlock-before-prepare, graph-lock reacquisition before
commit, preallocated page insertion in both runtime roots, and absence of the
allocation-capable page-add call from each reseed body.  It also requires a
short-plan failure to finish its private nodes before any old-list detach.  The
adjacent physical finish test was tightened to slice the actual strict reseed
function and to select the post-detach commit unlock rather than the earlier
preparation-failure unlock.

## Implementation

- C has a private evacuation-node list preparer and a locked required-page
  counter.  Reseed loops until the private capacity survives locked
  revalidation, then consumes it through the existing allocation-free add
  primitive.
- Strict pcc-Python mirrors the same private-list count/prepare/revalidate
  protocol and adds a local allocation-free commit helper.
- Both roots preserve the old list until a complete plan exists and finish old
  plus surplus nodes after graph unlock.

## Focused evidence

All pytest commands stopped at the first failure.

1. The final source/C packet covers the ordering contract, the prior physical
   finish contract, two-page reseed twice in succession, and the established C
   page-policy/counter behavior:

   ```text
   gtimeout 90s sh -c 'set -o pipefail; env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend4_production.py::test_relocation_reseed_prepares_evacuation_nodes_before_locked_commit tests/python/test_gc_backend4_production.py::test_relocation_reset_retires_detached_nodes_after_graph_unlock tests/python/test_gc_backend4_production.py::test_c_reseed_rebuilds_multiple_evacuation_pages_from_prepared_nodes tests/python/test_gc_backend4_production.py::test_backend4_genzgc_page_policy_records_candidates_and_evacuated_bytes 2>&1 | tee build/gc4-a3b-reseed-plan-final.log'
   4 passed in 0.26s
   ```

2. The same non-empty, two-page, repeated-reseed probe linked against the
   strict pcc-Python production archive:

   ```text
   gtimeout 180s sh -c 'set -o pipefail; env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend4_production.py::test_strict_reseed_rebuilds_multiple_evacuation_pages_from_prepared_nodes 2>&1 | tee build/gc4-a3b-reseed-plan-strict-dynamic.log'
   1 passed in 123.07s
   ```

3. Strict `py_gc_backend.py` compiled under
   `--backend self --python-libpython=off --ir-scaffold=on --python-library`.
   Python syntax, C syntax with `PCC_WITH_THREADS=0/1`, and
   `git diff --check` passed.

4. Four true pthreads ran 64 rounds of reset/select versus telemetry reseed,
   then a final reset/select/reseed recovered 16 candidates, 2048 bytes, one
   evacuation page and zero errors in both runtime roots:

   ```text
   gtimeout 90s sh -c 'set -o pipefail; env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend4_production.py::test_relocation_reseed_prepares_evacuation_nodes_before_locked_commit tests/python/test_gc_backend4_production.py::test_c_concurrent_reset_reseed_revalidates_prepared_plan tests/python/test_gc_backend4_production.py::test_strict_concurrent_reset_reseed_revalidates_prepared_plan 2>&1 | tee build/gc4-a3b-reseed-plan-pthread-final.log'
   3 passed in 0.86s
   ```

   This is real concurrency stress but does not deterministically pause a
   selector in the plan-growth window.

An earlier two-node command emitted only the first C progress dot and no final
pytest summary because its session identifier was not retained.  Its process
was observed until exit, but it is not counted as green evidence; both nodes
were rerun separately with complete summaries above.

## Frozen identities

```text
70620fee4ffbeffaa80b7b6ecdce2276efb4a7b3919f914adb3c3421d57b7172  pcc/py_runtime/src/py_gc_backend.c
624597fc8518c921ea42736b83544a62e4465a6882b8e5c8d207771d467db766  pcc/py_runtime/py/py_gc_backend.py
b73b99b76bf8d28fbb64018febf3af9ee81c7fd340a9165fe40ed3665b5b8cb9  tests/python/test_gc_backend4_production.py
eac4db30e20d26708d7c1effac71fed2a918793acfc1ca82af8ab00c6abfba49  build/gc4-a3b-reseed-plan-final.log
4dbe35a12fc911cae1655616d964f9d4bb7af0deff232085bbfecf007b809015  build/gc4-a3b-reseed-plan-strict-dynamic.log
879df816113add5c5383d367a0cb64e865e400ac1137b9bb523fecd68135afec  build/gc4-a3b-reseed-plan-pthread-final.log
```

## Next boundary

Establish a lifetime-safe bounded protocol for the remaining relocation,
object and page scans rather than moving raw non-owning pointers past unlock.
Deterministically force the plan-growth revalidation window and allocation
failure before closing that holder.  A3c remains blocked until this and the
remaining GC3/callback/log holder inventory are source- and pthread-green.
