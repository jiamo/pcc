# GC4 A3b root-introspection tripwires

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b fatal-log holder sub-boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

The two scheduler-root and three continuation-root invariant checks in
`pcc_gc_scheduler_root_count` and
`pcc_gc_continuation_root_slot_count` now use the already proven deferred
graph-tripwire slot. They no longer enter runtime logging while their query
functions hold the graph lock; the fatal sink runs after outer physical
unlock. Strict pcc-Python has no matching locked diagnostics on these paths.

This closes five introspection checks only. Mixed-context object/instance/remap
visitors and other fatal-log sites remain separately open.

## RED and gates

The expanded source contract was genuinely RED on direct
`PCC_RT_TRIPWIRE` calls in both count functions (`1 failed in 0.09s`). Armed
threaded syntax passed. Final packet:

```text
gtimeout 240s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_runtime_tripwires.py::test_graph_locked_tripwires_defer_until_outer_unlock_source \
  tests/python/test_runtime_tripwires.py::test_armed_tripwires_accept_valid_roots_zpage_forwarding_and_native_handle \
  tests/python/test_gc_coroutine_scheduler_roots_production.py::test_scheduler_and_frame_root_observability_across_backends \
  tests/python/test_gc_coroutine_scheduler_roots_production.py::test_scheduler_root_handle_unregister_keeps_legacy_api_observable \
  2>&1 | tee build/gc-root-introspection-tripwire-final.log'

4 passed in 15.66s
```

The packet exercises armed valid roots plus scheduler/frame observability
across all five backends. `git diff --check` exited zero.

## Frozen identities

```text
ecd57fd0c296d15596783c1bf69ce4a8c1fba700bd912d22110eb387de51d3ae  pcc/py_runtime/src/py_gc_backend.c
092c8ce9d85bd77a345f7dc10ff32a55ec1dfac2c0d159e1078e54589ba00781  tests/python/test_runtime_tripwires.py
39866cc5a744875cca771a3d93d4923d801ce18d811b97ecc17d05dbcd84a529  build/gc-root-introspection-tripwire-final.log
```

## Next boundary

Do not connect A3c. Classify remaining mixed-context object/instance/remap
tripwires and build the owner-referent remembered-slot worklist.
