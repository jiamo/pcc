# GC4 A3b runtime-root snapshot bounded

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b public snapshot holder sub-boundary confirmed; parent
remains `IN_PROGRESS`.

## Claim boundary

The C `pcc_gc_visit_runtime_roots` snapshot no longer counts or fills every
registered root in one graph-lock tenure. One runtime-thread owner walks
frame, continuation, scheduler and builtin-cache slots through resumable
cursors, examining at most `PCC_GC_SAFEPOINT_BATCH == 16` slots per lock
transaction. Snapshot capacity grows outside the lock, and callbacks/decrefs
remain outside after ownership is released.

Frame, continuation and scheduler unlink paths repair a retained snapshot
cursor before node free. Continuation relocation retarget resets the active
slot offset. A default-off probe pauses only after a batch has unlocked and is
part of the tested runtime seam.

The real GC0 caller already owns the stopped world while it computes
reachability, so registered roots do not appear during its round. A direct
public call defines a forward-only round: a head inserted after that list was
passed is intentionally visible on the next call, while deletion of the next
cursor node takes effect immediately. This is proven below rather than hidden
as an atomic-snapshot claim.

Strict pcc-Python still has no owner for this public ABI. Trace-cycle roots,
recursive owner-referent promotion and remaining tripwire/log paths are not
closed here.

## RED

The source contract was genuinely RED because the snapshot still used
whole-registry count/fill helpers:

```text
tests/python/test_gc_update_referents.py::
test_runtime_root_snapshot_fill_has_bounded_graph_tenures_source

missing pcc_gc_runtime_root_snapshot_fill_batch_unlocked(
1 failed in 0.17s
```

## Runtime proof and gates

The true-pthread cursor probe registers 40 scheduler roots, pauses after the
first 16-slot unlocked batch, removes the exact next node (`handle[23]`) and
inserts a new head. The in-flight round observes exactly the other 39 original
roots and not the new head; the next call observes the new head and the same 39
survivors. The existing caller callback test also reentrantly unregisters its
own root and drops the original owner reference, while the 80-root test forces
unlocked heap growth.

Final packet:

```text
gtimeout 180s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_gc_update_referents.py::test_runtime_root_caller_visitor_uses_unlocked_owned_snapshot_source \
  tests/python/test_gc_update_referents.py::test_runtime_root_snapshot_fill_has_bounded_graph_tenures_source \
  tests/python/test_gc_update_referents.py::test_runtime_root_extension_traversal_runs_after_graph_unlock_source \
  tests/python/test_gc_update_referents.py::test_builtin_exception_cache_is_visible_as_a_runtime_root \
  tests/python/test_gc_update_referents.py::test_runtime_root_snapshot_heap_preserves_all_scheduler_roots \
  tests/python/test_gc_backend_generational.py::test_runtime_root_extension_traverse_runs_after_graph_unlock \
  tests/python/test_gc_backend_generational.py::test_runtime_registered_root_visitor_runs_after_graph_unlock \
  tests/python/test_gc_threading_substrate.py::test_runtime_root_snapshot_repairs_cursor_across_unlocked_batches \
  tests/python/test_gc_coroutine_roots.py::test_suspended_heap_frame_local_survives_collect_across_backends \
  2>&1 | tee build/gc-runtime-root-snapshot-bounded-final.log'

9 passed in 8.04s
```

C11 syntax passed with threads off and on. `git diff --check` exited zero.

## Frozen identities

```text
0e723b641e05c0f7e3c3a63fc4ebc17f3782e591e0f3f235f91af747b766bb89  pcc/py_runtime/src/py_gc_backend.c
97dcbb4ed4e6a24e118004fc037aa588eee0c755bf67f49dfc08d162a20205a8  tests/python/test_gc_update_referents.py
0844607592700c8655953a8831d2be102080d05db3b5c6b9f0e5e6a85f41133a  tests/python/test_gc_backend_generational.py
ef5ffa05fc93632b08f0de4503eb0cb4337f7fde56728d499564dc483e134afd  tests/python/test_gc_threading_substrate.py
f1a80754dbb457b7924d1d2d9fb0cc6ff246669260f760db297dfc8cb756e03e  build/gc-runtime-root-snapshot-bounded-final.log
```

## Next boundary

Do not connect A3c. Split trace-cycle extension traversal across the
initial/final mark protocol, design recursive owner-referent promotion as a
resumable remembered-slot worklist, and finish the locked tripwire/log
inventory.
