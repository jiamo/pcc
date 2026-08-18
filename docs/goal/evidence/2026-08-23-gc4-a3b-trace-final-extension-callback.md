# GC4 A3b trace final extension callback split

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b final-cut callback sub-boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

C and strict pcc-Python tracing finishers no longer invoke extension
`PyModuleDef.m_traverse` while holding the GC graph lock.

After the finisher owns STW, it validates the exact finish claim and publishes
state `3` with the claimed `(epoch, backend)`, then unlocks for extension
traversal. Each reported root reacquires the graph lock briefly and is grayed
only if final state, finish claim, cycle epoch, selected backend and mark-active
state still match. After traversal the finisher reacquires the graph lock,
revalidates the same token, clears it, rescans internal current roots, drains
all gray objects, and only then classifies white objects as sweep candidates.
Invalid or superseded claims clear only their matching token.

This establishes strict final-cut extension parity that was previously absent.
The final callback still runs while STW is owned, by design; this slice removes
the graph-lock callback edge and does not claim arbitrary extension code may
wait on a registered thread parked by that STW epoch.

## RED and denied probe

The source contract was genuinely RED because C finalization still called
`pcc_capi_visit_extension_module_state_roots` inside
`pcc_gc_finish_tracing_cycle`:

```text
tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py::
test_final_trace_extension_traversal_precedes_locked_cut_source

assert extension traversal not in locked finish
1 failed in 0.10s
```

The first strict runtime probe timed out with stage code `SWIFJG`: it reached
initial and final traverse, began joining, and the raw pthread began a managed
`pcc_gc_object_is_known` call. A second diagnostic emitted `SWIFJLKUG`: the
same raw pthread successfully acquired and released strict's real production
graph lock (`LKU`) and hung only after entering managed object lookup (`G`).
That lookup is an invalid oracle because an unregistered raw pthread may not
access managed owners/addresses. The accepted mode-specific probe keeps the C
oracle's `object_is_known` acquisition, while strict tests the production
`pcc_py_gc_minor_graph_lock/unlock` directly and does not touch a managed
object. Both use a raw pthread because registered threads are correctly parked
by the final STW owner.

## Gates

Direct strict self/no-libpython closure passed for the final scheduler. Final
current-source packet:

```text
gtimeout 180s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py \
  tests/python/test_freestanding_gc_common_mark_cycle.py \
  tests/python/test_gc_backend_generational.py::test_initial_trace_extension_traverse_runs_after_graph_unlock \
  tests/python/test_gc_backend_generational.py::test_pcc_python_initial_trace_extension_traverse_runs_after_graph_unlock \
  tests/python/test_gc_backend_generational.py::test_final_trace_extension_traverse_runs_after_graph_unlock \
  tests/python/test_gc_backend_generational.py::test_pcc_python_final_trace_extension_traverse_runs_after_graph_unlock \
  2>&1 | tee build/gc-trace-extension-final-cut.log'

18 passed in 141.23s
```

The packet includes C/strict initial and final true-pthread probes,
LLVM/self exact closures and both production archive owners. C11 syntax passed
with threads off and on. `git diff --check` exited zero.

## Frozen identities

```text
7d41786e1888aaebf1b0875bb7898e0ad5b2fdcd7cd364e1771b8abbc6847366  pcc/py_runtime/src/py_gc_backend.c
b743e59ee46a8ef5f3021b433e4bff25783084e96efe180476acf9949622b493  pcc/py_runtime/py/freestanding_gc_incremental_concurrent_scheduler.py
63ac4ec5fa9fe575cdd044d1d3c4d762990dc7abe4d486384a84504cd93281d6  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py
a6008592e0b0acfd01e01825d050ae584b67fa4a73d2e8321482ae4238352867  tests/python/test_gc_backend_generational.py
e59b898f56da60a24b8f1bef9b3e8bab64a91b1c23ab0395eeb44e73895a59dc  build/gc-trace-extension-final-cut.log
b527114b47bbf5cb6abd1b81240331e53197a5c627ee2d5abf8a49cbd28bb16b  /tmp/gc_trace_final_extension_scheduler.ll
```

## Next boundary

Do not connect A3c. Design recursive owner-referent promotion as a resumable
remembered-slot worklist and finish the remaining locked tripwire/log and
unbounded-holder inventory. Trace initial and final extension callbacks are
now split, but no broader owner-promotion or tripwire-clean claim is made.
