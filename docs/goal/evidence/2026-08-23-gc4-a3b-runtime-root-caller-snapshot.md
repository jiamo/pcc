# GC4 A3b runtime-root caller snapshot

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b public runtime-root callback sub-boundary confirmed; parent
remains `IN_PROGRESS`.

## Claim boundary

The C `pcc_gc_visit_runtime_roots` entrypoint no longer invokes its
caller-provided visitor under the GC graph lock. It now:

1. counts the registered frame, continuation, scheduler and builtin-cache
   slots under the lock;
2. allocates a snapshot outside the lock (using a 64-entry stack buffer for
   the common case);
3. revalidates and fills the snapshot under the lock while taking one
   temporary reference per non-null value;
4. unlocks, invokes the caller for every snapshotted value in the prior order,
   and releases each temporary reference outside the lock; and
5. performs the separately split extension-module traversal outside the lock.

Snapshot size overflow or allocation failure enters the fatal diagnostic sink
only after unlock; it cannot silently omit roots. Null-root callbacks remain
present, preserving the old slot-walker behavior.

This closes callback execution and callback-induced root-registry/lifetime
reentry. The count and fill passes are still proportional to the number of
registered slots and therefore do **not** establish a bounded graph-lock
tenure for this public entrypoint. Trace-cycle root/extension traversal and
owner-referent promotion remain separate. Strict pcc-Python has no owner for
this public ABI, so no strict parity claim is made.

## RED

The source contract was genuinely RED because the public function still
directly installed `visit` in its locked mapped-root context:

```text
tests/python/test_gc_update_referents.py::
test_runtime_root_caller_visitor_uses_unlocked_owned_snapshot_source

missing pcc_gc_runtime_root_snapshot_count_unlocked()
1 failed in 0.17s
```

## Runtime proof and gates

- A true-pthread callback wakes and joins a contender whose next operation
  acquires the real graph lock.
- In that callback, the current scheduler root is unregistered and the
  original owner reference is decrefed. The snapshot's temporary reference
  keeps the value alive until the post-callback release.
- An 80-scheduler-root probe forces the heap snapshot path and observes each
  registered root exactly once.
- The builtin exception cache remains visible, and a real suspended-frame
  collect probe remains green across all five backend selections.

Final packet:

```text
gtimeout 180s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_gc_update_referents.py::test_runtime_root_caller_visitor_uses_unlocked_owned_snapshot_source \
  tests/python/test_gc_update_referents.py::test_runtime_root_extension_traversal_runs_after_graph_unlock_source \
  tests/python/test_gc_update_referents.py::test_builtin_exception_cache_is_visible_as_a_runtime_root \
  tests/python/test_gc_update_referents.py::test_runtime_root_snapshot_heap_preserves_all_scheduler_roots \
  tests/python/test_gc_backend_generational.py::test_runtime_root_extension_traverse_runs_after_graph_unlock \
  tests/python/test_gc_backend_generational.py::test_runtime_registered_root_visitor_runs_after_graph_unlock \
  tests/python/test_gc_coroutine_roots.py::test_suspended_heap_frame_local_survives_collect_across_backends \
  2>&1 | tee build/gc-runtime-root-snapshot-final.log'

7 passed in 0.77s
```

C11 syntax passed with threads off and on. `git diff --check` exited zero.

## Frozen identities

```text
3c49e0ace8858d2fadc4d011cb3cc91365377da1d2d7c9ca828429f99a7f3b40  pcc/py_runtime/src/py_gc_backend.c
0f910cdfe8917833e2ebfbdc2181993a596c4020fa1527e552a70adb243c3c0e  tests/python/test_gc_update_referents.py
0844607592700c8655953a8831d2be102080d05db3b5c6b9f0e5e6a85f41133a  tests/python/test_gc_backend_generational.py
7fd7c876b8a943b35597fe3a71a6321cbda4befdd3c992515cbd66d4cf3157bd  build/gc-runtime-root-snapshot-final.log
```

## Next boundary

Do not connect A3c. Bound the public snapshot count/fill lock passes or record
why this entrypoint can only run under a separate stopped-world owner; split
trace-cycle extension traversal without weakening initial/final cuts; design
owner-referent work as a resumable remembered-slot worklist; and continue the
remaining tripwire/log inventory.
