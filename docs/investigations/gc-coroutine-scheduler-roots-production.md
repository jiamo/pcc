# Coroutine / scheduler roots production closure

This closes the shared GC gate for stackless Python coroutine/task roots and
scheduler roots across Backend 0..4.

## Existing gate

`tests/test_gc_coroutine_roots.py` already verifies:

- suspended heap-frame local survives `pcc_gc_collect(0)` across backends 0..4;
- task waiter cycles stay alive while reachable only from a suspended task;
- the same cycle is collectable after task completion;
- the same semantics hold for the pcc-Python runtime archive.

## Added observability

This patchset adds public root telemetry:

- `pcc_gc_scheduler_root_count()`
- `pcc_gc_frame_root_slot_count()`
- `pcc_gc_coroutine_root_score()`

and telemetry counters:

- `PCC_GC_COUNTER_SCHEDULER_ROOTS`
- `PCC_GC_COUNTER_FRAME_ROOT_SLOTS`
- `PCC_GC_COUNTER_COROUTINE_ROOT_SCORE`

The C runtime, pcc-Python runtime mirror, and codegen ABI are all wired.

## Added production test

`tests/test_gc_coroutine_scheduler_roots_production.py` verifies under every
backend 0..4:

- scheduler root registration is visible to telemetry;
- native frame root slots are visible to telemetry;
- scheduler queue entries survive collection and pop back as the same value;
- public symbols are wired into C, pcc-Python, and `runtime_abi.py`.

## Gate

```bash
bash scripts/run_coroutine_scheduler_roots_gate.sh
```

## Verdict

The pcc runtime now has a production gate for the shared stackless coroutine /
scheduler root model required before Backend #2, #3, and #4 can safely operate
with suspended program state outside the current C stack.

## Update: virtual-thread queue roots

Status: resolved on 2026-05-17.

The root model now covers the concrete virtual-thread scheduler queues, not
only the generic scheduler-root queue substrate. Ready-queue, timer-queue, and
IO-wait entries register their `PyObject **thread` slot with the scheduler root
registry for the lifetime of the entry. Timer and IO wait entries also mark the
virtual thread as queued while parked, then clear that state before moving it
back to the ready queue.

The regression in `tests/python/test_gc_coroutine_roots.py` constructs a
continuation with a heap slot, wraps it in a virtual thread, queues it through
ready/timer/IO paths, releases local references, runs `pcc_gc_collect(0)`, then
verifies the continuation slot after dequeue/wakeup across backends 0..4. The
same probe runs against the C runtime archive and the pcc-Python runtime
archive.

Validation:

```text
tests/python/test_gc_coroutine_roots.py
6 passed in 110.51s

tests/python/test_virtual_threads_gap.py tests/python/test_gc_coroutine_scheduler_roots_production.py
11 passed in 21.27s
```
