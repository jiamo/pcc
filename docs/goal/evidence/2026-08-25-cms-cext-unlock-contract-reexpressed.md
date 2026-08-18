# CMS cext-unlock contract re-expressed — 2026-08-25

## Claim

`test_incremental_trace_cext_claim_unlocks_callback_and_revalidates_commit`
asserts the unlock-before-unlocked-callback property again, in the three places
that property now lives.  Closes
`GC-P1-CMS-CEXT-UNLOCK-CONTRACT-MARKER-DRIFT`.  **No runtime file was changed**
— the property already held; the test could no longer see it.

## What had drifted

The test sliced `pcc_gc_tracing_step_cycle` up to the next `@c_abi_export` and
searched for `pcc_gc_trace_cext_referents_unlocked(`.  It raised
`ValueError: substring not found`, so it was failing *before asserting
anything* — the contract was unverified rather than violated.

Cext tracing no longer runs inside the step cycle at all.  The step now **hands
off** a pending object and returns; the callback runs in its own exported
helper, `_pcc_gc_trace_cext_complete_context`.

## The property, verified where it now lives

Read from
`pcc/py_runtime/py/freestanding_gc_incremental_concurrent_scheduler.py`:

```text
_pcc_gc_trace_cext_complete_context (269)
  273  pcc_gc_trace_cext_referents_unlocked(cext_obj, cext_ctx)   <- callback
  277  pcc_py_gc_minor_graph_lock()                               <- lock AFTER
  308  pcc_py_gc_minor_graph_unlock()

_pcc_gc_drain_all_gray_stopped_world (315)
  322  pcc_py_gc_minor_graph_lock()
  353  pcc_py_gc_minor_graph_unlock()                             <- unlock
  356  _pcc_gc_trace_cext_complete_context(cext_ctx)              <- then call

pcc_gc_tracing_step_cycle (633)
       unlocks before returning when a pending cext object exists
```

The graph lock is not held across the callback on any of the three paths, so
the property holds.

## The re-expression

Three assertions replace the one that could no longer resolve:

1. the step cycle does not hand off while still holding the lock;
2. the completion helper runs the callback before taking the lock;
3. its stopped-world caller releases the lock before invoking it.

The five state tokens the test also required were split by current owner: four
remain in the step cycle, and `(cext_flags & ~56) | 32` moved with the callback,
so it is now asserted in the helper alongside the gray-count decrement and the
pending-object clear rather than being dropped.

This is stronger than the original single ordering check, which only covered
one function.

## Gates

```text
test_incremental_trace_cext_claim_unlocks_callback_and_revalidates_commit
  1 passed in 0.41s

tests/python/test_gc_threading_substrate.py
  (aging gate deselected)   188 passed, 4 skipped, 2 deselected in 124.23s
```

The previous full-suite figure was `187 passed, 3 deselected`; this test is now
back in the suite and passing rather than routed around.

## Nonclaims

- No runtime change, and no assertion was weakened — the ordering check went
  from one function to three.
- The four skips are pre-existing and unrelated.
- The aging gate remains deliberately red; see
  `2026-08-25-backend4-aging-midstop-expectation.md`.
