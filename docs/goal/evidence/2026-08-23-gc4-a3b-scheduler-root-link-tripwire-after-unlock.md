# GC4 A3b scheduler-root link tripwire after unlock

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b fatal-log holder sub-boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

The C scheduler-root link transaction no longer calls the fatal runtime
tripwire/logging sink while holding the GC graph lock. The locked helper links
the node and computes a two-bit invariant result only. Both callers — direct
root registration and scheduler-queue publication — unlock before reporting a
nonzero result through `pcc_runtime_tripwire_fail`.

The checks remain compile-time armed by `PCC_RUNTIME_TRIPWIRES`; normal builds
still do not evaluate them. Strict pcc-Python had no equivalent tripwire/log
call in its locked link helper, so no mirror change was required.

This closes only the two scheduler-root **link** invariant reports. Other
locked tripwire/log sites, root visitors, owner-referent promotion,
trace-cycle extension traversal, caller runtime-root callbacks, A3c, raw
container transactions and collector-owned STW remain open.

## RED

The focused source contract was genuinely RED because the locked helper still
returned `void` and contained both `PCC_RT_TRIPWIRE` calls:

```text
tests/python/test_freestanding_gc_root_registry.py::
test_scheduler_root_link_tripwire_reports_only_after_graph_unlock

IndexError: static int32_t pcc_gc_scheduler_root_link_locked was absent
1 failed in 0.10s
```

## Gates

The C source compiled in both ordinary threads-off mode and armed threads-on
mode (`-DPCC_RUNTIME_TRIPWIRES`). The final focused packet was:

```text
gtimeout 240s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_root_registry.py::test_scheduler_root_link_tripwire_reports_only_after_graph_unlock \
  tests/python/test_runtime_tripwires.py::test_tripwire_source_covers_named_runtime_boundaries \
  tests/python/test_runtime_tripwires.py::test_armed_tripwires_accept_valid_roots_zpage_forwarding_and_native_handle \
  2>&1 | tee build/gc3-scheduler-root-tripwire-final.log'

3 passed in 7.44s
```

The armed runtime probe exercises valid scheduler-root registration together
with continuation roots, ZPage forwarding and native-handle release; the
diagnostic messages remain present and the valid path does not abort.
`git diff --check` also exited zero.

## Frozen identities

```text
c78d7659cc47ba8e8f4403c0e4a0dd31ea2feb6fe07048f8b5acf2ff3fe5183e  pcc/py_runtime/src/py_gc_backend.c
664327c30a9016b8b9333958f0412461625745e56bad9141c148829316b84015  tests/python/test_freestanding_gc_root_registry.py
4fcac4a56de2da841384ce0300f0213c20ec263c4ae518114313f0784257875f  build/gc3-scheduler-root-tripwire-final.log
```

## Next boundary

Do not connect A3c. Continue the locked-holder inventory with
owner-referent promotion, trace/runtime-root callbacks and the remaining
tripwire/log sites; treat recursive per-object slot promotion as a worklist
design problem rather than truncating a semantic closure.
