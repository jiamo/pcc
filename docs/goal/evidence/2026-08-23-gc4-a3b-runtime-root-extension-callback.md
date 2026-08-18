# GC4 A3b runtime-root extension callback split

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b runtime-root callback sub-boundary confirmed; parent
remains `IN_PROGRESS`.

## Claim boundary

The C `pcc_gc_visit_runtime_roots` entrypoint now releases the GC graph lock
before invoking `pcc_capi_visit_extension_module_state_roots`. A native
extension's external `PyModuleDef.m_traverse` callback therefore no longer
runs inside that entrypoint's graph-lock tenure.

This moves only the extension traversal. The caller-provided root visitor is
still invoked for frame, continuation, scheduler and builtin-cache roots
while the graph lock is held; that distinct callback boundary remains open.
Trace-cycle extension traversal in `pcc_gc_gray_current_roots` also remains
open. The strict pcc-Python runtime has no owner for the public
`pcc_gc_visit_runtime_roots` entrypoint, so this slice is C-only and makes no
strict parity claim.

## RED

The source contract was genuinely RED on the old order:

```text
tests/python/test_gc_update_referents.py::
test_runtime_root_extension_traversal_runs_after_graph_unlock_source

assert unlock_index < extension_visit_index
1218 < 1157
1 failed in 0.18s
```

## Runtime proof and gates

A real threaded C runtime probe creates a `PyModuleDef` with module state and
an `m_traverse` callback. The callback wakes and joins a contender whose next
operation calls `pcc_gc_object_is_known` and therefore acquires the real graph
lock. The join completes, proving traversal is outside the holder.

Final packet:

```text
gtimeout 120s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_gc_update_referents.py::test_runtime_root_extension_traversal_runs_after_graph_unlock_source \
  tests/python/test_gc_update_referents.py::test_frame_and_continuation_roots_share_mapped_root_slot_walker_source \
  tests/python/test_gc_update_referents.py::test_scheduler_roots_share_single_root_slot_walker_source \
  tests/python/test_gc_update_referents.py::test_builtin_exception_cache_is_visible_as_a_runtime_root \
  tests/python/test_gc_backend_generational.py::test_runtime_root_extension_traverse_runs_after_graph_unlock \
  2>&1 | tee build/gc-runtime-root-extension-unlock-final.log'

5 passed in 7.50s
```

C11 syntax passed with threads off and on. `git diff --check` exited zero.

## Frozen identities

```text
0c0431b9e9470f67346c337cfaad8c8590ce3500e491a577e12a4e26128561f8  pcc/py_runtime/src/py_gc_backend.c
add29abd4d03b55e6d89264abd8313c229e6162b53005bf23db9c7358ee57a21  tests/python/test_gc_update_referents.py
cbeb3c062f3f55446e69bca80daef0664bdee205b138cbba93b42c4c59bdf656  tests/python/test_gc_backend_generational.py
3bbeeb21cc597cbbc5ec2766bcd70592f8eb4d646c1bb18fefa2585f4844d4fa  build/gc-runtime-root-extension-unlock-final.log
```

## Next boundary

Do not connect A3c. Split or snapshot the caller visitor for registered roots,
and independently split trace-cycle extension traversal without weakening the
initial/final mark cut. Owner-referent worklist design and remaining
tripwire/log holders also remain open.
