# GC4 A3b C-extension incremental trace claim

Date: 2026-08-24

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite incremental-trace callback boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

The ordinary incremental gray-object cursor no longer invokes C-extension
`tp_traverse` under the GC graph lock. It retains and claims one non-moving
C-extension owner with exact object/cycle/backend state, advances the cursor,
runs the external callback unlocked, performs each reported-slot gray action in
one revalidated short transaction, and commits gray-count/BLACK state only
after a final claim revalidation. C and strict pcc-Python mirror the protocol.

## RED

The focused source contract was RED on repository HEAD:

```text
git show HEAD:pcc/py_runtime/src/py_gc_backend.c |
  rg 'pcc_gc_trace_cext_claim_unlocked|pcc_gc_trace_cext_complete'
# no output

git show HEAD:pcc/py_runtime/py/freestanding_gc_incremental_concurrent_scheduler.py |
  rg 'pcc_gc_trace_cext_referents_unlocked|pcc_gc_trace_cext_pending_obj'
# no output
```

The dynamic test uses sixteen newer filler roots so cycle startup occurs while
the callback is disarmed and the C-extension object remains behind the
budget-one cursor. Once armed, `tp_traverse` wakes a true runtime thread whose
next operation takes the production graph lock; C and strict both prove the
lock is available during the external callback.

## Gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_freestanding_gc_common_mark_cycle.py \
  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py \
  tests/python/test_gc_threading_substrate.py::test_incremental_trace_cext_traverse_runs_outside_graph_lock

16 passed in 5.14s
```

The final cold threaded strict node required a measured 210-second inner
budget after a 150-second wrapper reached its watchdog before pytest could
flush a summary:

```text
gtimeout 240s zsh -o pipefail -c "gtimeout 210s env -u LC_ALL \
  uv run pytest -vv -x -n0 --tb=short \
  'tests/python/test_gc_threading_substrate.py::test_incremental_trace_cext_traverse_runs_outside_graph_lock[pcc_python]' \
  2>&1 | tee build/gc-trace-cext-claim-strict.log"

1 passed in 148.57s
```

The superseded combined command had no final summary and no surviving child;
it is not evidence.

Adjacent final-source packets:

```text
3 passed in 1.78s    production collector link-map ownership
23 passed in 6.16s   promotion + incremental trace callback and GC3/GC4
                     owner-worklist source/pthread neighbors
24 passed in 7.92s   relocation-payload plus forwarding-retirement task gate
```

C syntax under `PCC_WITH_THREADS=0/1`, strict self/no-libpython closure,
Python syntax and `git diff --check` pass.

## Frozen identities

```text
f117c17b9a3977372d702b49a4322f81b2ff0d44ca384cbb9a2b80fffb4c59c5  pcc/py_runtime/src/py_gc_backend.c
058e714c4830daa05358324df005bf5a5ff9b127b393ec979187f35809d058fe  pcc/py_runtime/py/freestanding_gc_common_mark_cycle.py
b3aed0ceca2c85993e9eddda64cb92d1f522c0162e9aabe25ed6224554de1460  pcc/py_runtime/py/freestanding_gc_incremental_concurrent_scheduler.py
44d37e0b58f07f7f08ff4588245bed21b5279a0ffb65a88e2b2f4cca670f70ae  pcc/py_runtime/py/freestanding_gc_state.py
98018b972de4fc5a94d8cf0195d2e5248fea3c645ed93eea43f264dcaf6b2bac  pcc/py_frontend/codegen/runtime_abi.py
dc93d5e0f2af45b7a0e0ad60e769d6385dbf78972b94d9d3a0a3d7a4e2f12ab5  tests/python/test_gc_threading_substrate.py
c1aada5d730c3dbb9be39685cd80017beee9983d8d9c07aeb1aebc08338747d7  build/gc-trace-cext-claim-strict.log
5a3e904ca331a2584f197d50f3af7ea14a090c54f02a4131336653370ab25d69  build/gc3-owner-worklist-runtime-final.log
0128e1b06cfcb7010abff5c6c385fb9246b2404c73f80fc474f5f430d086daf3  build/gc4-relocation-mutator-quiescence.log
c78ce6a64547df363617285bde5ad2a469b9260e48b180071b4e3985e2f008dd  nonthreaded final cache provenance
d19986ca373113535d9668125e77eaaa4655bb9d6cc684508bd1a8c254808dc4  threaded final cache provenance
```

Final cache roots:

```text
/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/95039e371b7be3a2bc8ad5cf-pcc-py
/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/95039e371b7be3a2bc8ad5cf-threaded-pcc-py
```

## Nonclaims

Initial refcount-root subtraction, final/CMS whole-gray drains, direct CMS gray
tickets, Backend-4 remap/update callbacks, A3c, raw transactions,
collector-owned STW, source/page lifetime, ABI raw leases, resurrection,
stage2 performance, fixed point and broad five-GC parity remain open.
