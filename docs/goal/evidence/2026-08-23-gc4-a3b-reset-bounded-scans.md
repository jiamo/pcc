# GC4 A3b relocation-reset bounded scans

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b sub-boundary confirmed; parent task remains `IN_PROGRESS`.

## Claim boundary

Backend 4 relocation reset in the C and strict pcc-Python runtime roots no
longer holds the graph lock while walking an unbounded relocation-node,
evacuation-page-node, or object-registry list. One reset owner spans the
unlocked batch boundaries. Each graph-lock tenure consumes at most 16 nodes,
physically finishes detached nodes after unlock, reaches a safepoint between
non-final batches, and resumes from an object-registry cursor whose unlink path
advances it before node recycling.

New relocation candidates and forwarding commits fail closed while reset owns
the multi-batch epoch. A competing reset owner waits outside the graph lock and
reaches a safepoint; same-owner re-entry returns without disturbing the outer
reset. Completion clears counters and releases ownership under the graph lock.

This closes only reset's three raw-node walks. Relocation-epoch reseed still
performs unbounded relocation/page counting and commit scans under the graph
lock. Its plan-growth revalidation window and allocation failure are not yet
forced deterministically. GC3/callback/log holders, A3c, raw container
transactions, backend switching, and collector-owned stopped-world execution
remain open.

## Genuine RED and fail-closed closure finding

`test_relocation_reset_batches_raw_node_scans_with_owned_cursor` was added
before implementation. It failed on the missing reset-owner protocol:

```text
1 failed in 0.32s
ValueError: substring not found
```

After the source shape was implemented, the strict no-libpython closure then
rejected the new cursor exactly as designed:

```text
PCC-PY-COMPILE-001: freestanding module emitted managed-runtime reference
... load ptr, ptr @pcc_gc_backend4_reset_object_cursor
```

The fix registered only the new raw `i64` owner and raw pointer cursor in the
existing freestanding GC ABI inventory and extended the exact-import ownership
tests. The managed-reference rejection itself was not weakened.

## Implementation

- C and strict reset acquire `pcc_gc_backend4_relocation_reset_owner`, process
  relocation and evacuation lists in 16-node detach/finish batches, then scan
  object nodes in 16-node cursor batches.
- Object-node unlink advances `pcc_gc_backend4_reset_object_cursor` while the
  graph lock still owns the node, preventing cursor UAF/ABA when another thread
  frees the node between reset batches.
- Candidate selection and both forwarding-install commit paths reject Backend
  4 admission while the reset epoch is active. Newly inserted object nodes do
  not need retroactive scanning because forwarding admission is closed and a
  fresh object cannot already carry a relocation-target flag.
- Backend switching's bulk object-list clear nulls the reset cursor. This is a
  defensive lifetime guard, not a claim that concurrent backend switching is
  supported by this slice.

## Focused evidence

All pytest commands stopped at the first failure.

1. Exact raw-global ownership checks passed after the fail-closed inventory
   repair:

   ```text
   gtimeout 30s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_freestanding_gc_object_nodes.py::test_object_nodes_have_one_strict_source_owner tests/python/test_freestanding_gc_relocation_selector.py::test_relocation_selector_has_one_strict_source_owner tests/python/test_freestanding_gc_forwarding_identity.py::test_forwarding_identity_has_one_strict_source_owner tests/python/test_freestanding_gc_state.py::test_gc_state_storage_types_are_registered_in_runtime_abi
   4 passed in 0.07s
   ```

2. Each affected strict module compiled under
   `--backend self --python-libpython=off --ir-scaffold=on --python-library`.
   The durable module list is in
   `build/gc4-a3b-reset-bounded-closures.log`.

3. The final C/source packet covers the source contract, prior physical finish
   and reseed-plan contracts, page-policy reset, target phase reset, repeated
   two-page reseed, and four true pthreads running 64 reset/select-versus-reseed
   rounds. The final 24-object state forces more than one 16-node relocation
   and object scan batch:

   ```text
   gtimeout 240s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend4_production.py::test_relocation_reset_retires_detached_nodes_after_graph_unlock tests/python/test_gc_backend4_production.py::test_relocation_reseed_prepares_evacuation_nodes_before_locked_commit tests/python/test_gc_backend4_production.py::test_relocation_reset_batches_raw_node_scans_with_owned_cursor tests/python/test_gc_backend4_production.py::test_backend4_genzgc_reset_relocation_set_clears_page_policy_shape tests/python/test_gc_backend_relocating.py::test_colored_relocating_targets_wait_for_phase_reset tests/python/test_gc_backend4_production.py::test_c_reseed_rebuilds_multiple_evacuation_pages_from_prepared_nodes tests/python/test_gc_backend4_production.py::test_c_concurrent_reset_reseed_revalidates_prepared_plan 2>&1 | tee build/gc4-a3b-reset-bounded-c-final.log'
   7 passed in 14.29s
   ```

4. The strict production runtime passed the matching target reset, repeated
   non-empty reseed and 24-object true-pthread multi-batch probe:

   ```text
   gtimeout 300s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_targets_wait_for_phase_reset tests/python/test_gc_backend4_production.py::test_strict_reseed_rebuilds_multiple_evacuation_pages_from_prepared_nodes tests/python/test_gc_backend4_production.py::test_strict_concurrent_reset_reseed_revalidates_prepared_plan 2>&1 | tee build/gc4-a3b-reset-bounded-strict-final.log'
   3 passed in 246.98s
   ```

5. Python syntax, C syntax with `PCC_WITH_THREADS=0/1`, and
   `git diff --check` passed.

An earlier broad source-ownership command reached five progress dots but its
session identifier was not retained and it produced no final pytest summary.
It is not counted as evidence; the affected exact-import checks and direct
strict module closures were rerun with complete results above.

## Frozen identities

```text
9b0a6f2245748e5a7fb21fd53ac2990daeba2b5961797be3cf03756a0cd683ae  pcc/py_runtime/src/py_gc_backend.c
d2738e1ca277d42cd54fdc1d08b572c3abdf6f9f1e2903fddf4c45b1c6379ab8  pcc/py_runtime/py/py_gc_backend.py
152491c443da48627492d377971d4534265639d4700b4dda732783f1d3a05372  pcc/py_runtime/py/freestanding_gc_state.py
2f96cca679e423ba8f765486f0030353ceacbc14add510c4e44510d3dfff3e84  pcc/py_runtime/py/freestanding_gc_object_nodes.py
455f067b044c80deb2248db3a4c4bb7c66c1dc44c89ab90a56c56d9b2595e09f  pcc/py_runtime/py/freestanding_gc_relocation_selector.py
b50cfcfee0afad5949a99cc898776974593c0bb6d4b5e8a727297e5bd2899225  pcc/py_runtime/py/freestanding_gc_forwarding_identity.py
96c25cd87282b8b2171ad1c5c3f5e005f8d7dd3289cab082232a7d28429a07e6  pcc/py_frontend/codegen/runtime_abi.py
0739d9cfa747903a1af0c556252fb51d08d01a49688b2724a13e64f68efc19c4  tests/python/test_gc_backend4_production.py
0ec9f1a350adc00a447fb936640e9dbec78d5c47a398cd720ab2fd908cf30442  build/gc4-a3b-reset-bounded-closures.log
08a61d5092d0db1cea3d9e28305f92c570d47d75b6a6b4443c0831432498b849  build/gc4-a3b-reset-bounded-c-final.log
d6d3f969dbe6e37b4e5afd7306c2c049d3713fba50837a3acd384e418236ae7e  build/gc4-a3b-reset-bounded-strict-final.log
```

## Next boundary

Deterministically pause relocation-epoch reseed between its locked required-
node count and commit revalidation, force concurrent plan growth, and inject a
private-node allocation failure. Then bound or split its remaining raw
relocation/page scans without carrying non-owning pointers across unlock.
A3c remains blocked until this and the remaining GC3/callback/log holder
inventory are source- and pthread-green.
