# GC4 A3b trace initial extension callback split

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b initial-mark callback sub-boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

Initial root seeding for tracing backends no longer invokes extension
`PyModuleDef.m_traverse` while holding the GC graph lock in either C or strict
pcc-Python.

Beginning a mark cycle publishes a pending `(epoch, backend)` token. Exactly
one scheduler claimant changes it to active, unlocks, invokes extension
traversal, and uses a runtime-owned callback that reacquires the graph lock for
one reported root only after revalidating token, epoch, backend and mark-active
state. Other steps return while traversal is active. On successful completion
the claimant clears the token and resumes ordinary trace work. Backend switch
and mark completion clear stale tokens. The C CMS worker follows up through
the same unlocked wrapper after releasing its own graph lock.

The C final white-to-sweep-candidate cut still performs its extension rescan
under graph lock and STW, deliberately preserving the pre-existing final-cut
semantics for the next slice. Strict final-cut extension parity was already
absent and is not claimed. Post-finalizer reachability recheck in C preserves
its extension rescan on the existing graph-unlocked STW path.

## RED

The source contract was genuinely RED because
`pcc_gc_gray_current_roots` still contained the extension traversal:

```text
tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py::
test_initial_trace_extension_traversal_runs_outside_graph_lock_source

assert pcc_capi_visit_extension_module_state_roots not in c_gray
1 failed in 0.10s
```

## Runtime proof and gates

Real C and strict threaded probes select Backend 1, install module state with
an external `m_traverse`, and make the first traversal wake and join a
contender whose next operation acquires the real graph lock. Both pass. The C
final cut may traverse the module again, but the probe's one-shot join flag
ensures the result is attributed only to the initial pending/claimed phase.

Direct strict self/no-libpython closure passed for the scheduler, common mark
owner and state modules. The first strict production runtime run passed after
a 125.75-second cold build. The final cached packet was:

```text
gtimeout 90s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py \
  tests/python/test_freestanding_gc_common_mark_cycle.py \
  tests/python/test_gc_backend_generational.py::test_initial_trace_extension_traverse_runs_after_graph_unlock \
  tests/python/test_gc_backend_generational.py::test_pcc_python_initial_trace_extension_traverse_runs_after_graph_unlock \
  2>&1 | tee build/gc-trace-extension-initial-final.log'

15 passed in 3.80s
```

C11 syntax passed with threads off and on. `git diff --check` exited zero.

## Frozen identities

```text
fe6e11b770845e1370d0473b6812b8145fa2f5aa894ef521a7c6cd62a27b111c  pcc/py_runtime/src/py_gc_backend.c
bbe385d3e9fa300a308bc3a82cbd4ada2d6994191cd4b4e61df7c530103a0cff  pcc/py_runtime/py/freestanding_gc_state.py
71ec0517fe4e19bbc07ab9ac4c3139c5913ab25a751956d9eb29920e1aefb254  pcc/py_runtime/py/freestanding_gc_common_mark_cycle.py
6fcde0567da516ae96677d7a66e589cf6dcd77c265f693db16a89794e62e7cc5  pcc/py_runtime/py/freestanding_gc_incremental_concurrent_scheduler.py
3fea9c75e9ac6b63c8504c2153496fac44f515ffa51c0ce52f5e18e1c970acf1  pcc/py_runtime/py/py_gc_backend.py
896b17b1973695f722e296ef30f646e3a89d9dd98bbc8b88bc8b0f96340b1904  pcc/py_frontend/codegen/runtime_abi.py
f4d0c075b2a157e23ffd0206651206e1f68d30c0d106889c7fe80626d47d0c57  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py
d2d854a9bb3a6117ac48875717d4e5bfdaba539c115966db1769dc2d56d712dc  tests/python/test_freestanding_gc_common_mark_cycle.py
c15761459fea6a50bb7179cc5a645dfcc3b6f49f33489ec9598ef599a1085e72  tests/python/test_freestanding_gc_state.py
dcccb423784b5ad947b10c3c6870d7fca1918142043c5f4d2610a9b0c2880e15  tests/python/test_gc_backend_generational.py
394d237b6937ecd356c96e49f6975759bed17c8e74e35a043659c1e94ba21580  build/gc-trace-extension-initial-final.log
d8e71bc9c38cca6253dfa276276f3a1609d5c3219ecdc239fb7b95edbca5bd6e  /tmp/gc_trace_extension_trace_scheduler.ll
cf6059618fcbd9e2ed5c5a411d749c31c7abd634f76d00448df861fe6e4f675a  /tmp/gc_trace_extension_common_mark.ll
b87063d415e3d9c890f3c8f3e33471959e45cd01c1c5275ebeaef4d745174fa8  /tmp/gc_trace_extension_state.ll
```

## Next boundary

Do not connect A3c. Split the final-cut extension rescan without allowing
white-to-candidate classification before all reported roots are grayed and
drained; establish strict final-cut parity. Owner-referent worklist design and
remaining tripwire/log holders also remain open.
