# GC4 A3b object-registration node/index plan

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b sub-boundary confirmed; parent task remains `IN_PROGRESS`.

## Claim boundary

C and strict pcc-Python object registration now prepare an object node and any
required object-index table capacity outside graph-lock ownership.  They then
reacquire the lock, revalidate the pool/index state, commit only a still-needed
capacity plan and insert through an allocation-free primitive.  A racing thread
that wins publication causes unused prepared storage to be freed only after the
loser unlocks.  The graph-leaf path still applies its flags without allocating
an object node or index table.

This evidence does **not** claim that object registration is wholly
allocation-free while graph-locked.  Its Backend 4 ZPage-tracking tail can
still allocate a ZPage node, owner-index capacity, and malloc-backed fallback
page/span under the outer registration lock.  Relocation reset, GC3 promotion
and remembered-owner work, callbacks, tripwire/log paths, A3c no-park
connection, raw container transactions and collector-owned STW also remain
open.

## Genuine RED

Before the implementation, the new source-order contract
`test_object_registration_prepares_node_and_index_before_graph_lock` failed
because neither runtime root had an object-node preparation boundary.  The
focused run reported `1 failed in 0.09s`; it failed while locating
`_object_node_prepare()` in the strict registration body.  This was a genuine
RED on the former allocation-capable shape, not an inferred failure from the
later implementation.

## Implementation

- `pcc_gc_object_node_prepare`,
  `pcc_gc_object_node_plan_requires_prepare`, and
  `pcc_gc_object_node_take_prepared` separate physical node allocation from
  allocation-free pool selection/commit.
- `pcc_gc_object_index_plan_capacity`,
  `pcc_gc_object_index_plan_commit`, and
  `pcc_gc_object_index_insert_preallocated` provide the same prepare/revalidate/
  commit contract for the open-addressed object index.  Commit transfers the
  replaced table back to the caller for post-unlock retirement.
- `pcc_gc_note_object_allocated_sized` in both runtime roots loops until its
  prepared resources satisfy current locked state, publishes flags/node/index
  exactly once, and releases unused/replaced resources after unlock.
- The C header and strict cross-object ABI table expose the same signatures.

## Focused evidence

All pytest commands used `-x -n0`; long runs used visible node IDs, a short
traceback and durable live output.

1. Full index/node C-oracle, LLVM/self and production-owner packet:

   ```text
   gtimeout 150s zsh -o pipefail -c 'gtimeout 120s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_freestanding_gc_index_table.py tests/python/test_freestanding_gc_object_nodes.py 2>&1 | tee build/gc4-a3b-object-registration-focused-final.log'
   11 passed in 5.79s
   ```

2. Production archive GC0..GC4 tracking parity on the current runtime roots:

   ```text
   gtimeout 420s zsh -o pipefail -c 'gtimeout 390s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_freestanding_gc_tracking.py::test_production_archive_uniquely_owns_tracking_and_matches_c_oracle_gc0_to_gc4 2>&1 | tee build/gc4-a3b-object-registration-plan.log'
   1 passed in 129.08s
   ```

3. Sixteen-way true-pthread cold-page race using content-addressed threaded C
   and strict archives.  Besides the ZPage publication invariant, every worker
   traverses concurrent object-node and initial object-index preparation:

   ```text
   gtimeout 420s zsh -o pipefail -c 'gtimeout 390s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_freestanding_gc_zpage_allocation.py::test_zpage_first_page_race_publishes_one_page_in_c_and_strict_runtime 2>&1 | tee build/gc4-a3b-object-registration-pthread.log'
   1 passed in 130.90s
   ```

4. Strict closure and static checks passed:

   ```text
   gtimeout 120s env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on --python-library --emit-llvm=/tmp/pcc-gc4-object-registration.ll pcc/py_runtime/py/py_gc_backend.py
   gtimeout 60s env -u LC_ALL uv run python -m py_compile <changed strict/test files>
   gtimeout 60s clang -std=c11 -Ipcc/py_runtime/include -Ipcc/py_runtime/src -fsyntax-only pcc/py_runtime/src/py_gc_index_table.c pcc/py_runtime/src/py_gc_backend.c
   gtimeout 60s clang -std=c11 -DPCC_WITH_THREADS=1 -Ipcc/py_runtime/include -Ipcc/py_runtime/src -fsyntax-only pcc/py_runtime/src/py_gc_index_table.c pcc/py_runtime/src/py_gc_backend.c
   gtimeout 20s git diff --check
   ```

No `pytest`, bootstrap or compiler child remained after the long gates.

## Frozen identities

```text
d467bfcc4e1710d716b9125610b01675232a1cb9a0b95f9554cfa7f80803628b  pcc/py_runtime/src/py_gc_backend.c
9af29a3925a3c549fae8c2b1294a0f0a30acf27dea3b6283d609582bfe3d4f90  pcc/py_runtime/src/py_gc_index_table.c
3964326b70d50e35c2466a79b82f8f45ab788e03f309ada5661a581f1bde1ecd  pcc/py_runtime/src/py_internal.h
ee0f6ba2b76b2a70c4e45a6b82fb9900e72c4522fb8f8861d3ec7645c0b10c66  pcc/py_runtime/py/freestanding_gc_index_table.py
dff2245885d14745892c996074de32954ca3f567aee2f5a432881024f9fb2880  pcc/py_runtime/py/freestanding_gc_object_nodes.py
640b1a6cc8b923363ffd9237b1a0b33e9cef03cce177c0f12b56af5e81b63ca5  pcc/py_runtime/py/py_gc_backend.py
719d6939ac58e8efd0e9562fa3af237a6050fedcd6c453e77ba7acbe531a1192  pcc/py_frontend/codegen/runtime_abi.py
980e7932aee69e8ac9984eedcb200817c5036c542393eef58afbca786d87f017  tests/python/test_freestanding_gc_index_table.py
2d03b89169179445d64f13139e3b6477aa65cf3fa22d4cc6d510bc9d59d79e26  tests/python/test_freestanding_gc_object_nodes.py
c0bf74d85fcac5a1120a9ea3249d2198cab0a9ca412c5bb368c032a364057549  tests/python/test_freestanding_gc_zpage_allocation.py
9fb2a6313de0b4552b618826870e71ad4e8dfb3ed83b9e1e4207d2cac1849eec  build/gc4-a3b-object-registration-plan.log
1e1b3e79fd36617828d5c79d0a8afa583a04b9670e185ba83e22ad8baec0b83b  build/gc4-a3b-object-registration-pthread.log
03a8de1ff5dda5cea140eee4bf40ee0ed26098ca034f935f7d71724a97ee6ab8  build/gc4-a3b-object-registration-focused-final.log
```

## Next boundary

Prepare the Backend 4 ZPage tracking node, owner-index capacity and any
malloc-backed fallback page/span outside the outer object-registration graph
lock.  Revalidate and commit allocation-free while preserving pending raw
allocation handoff, active-page admission, owner-index insertion, node-pool
reuse, concurrent growth and failure rollback.  Only after that path is green
does the A3b inventory proceed to relocation-reset retirement and the remaining
GC3/callback/log/unbounded holders.
