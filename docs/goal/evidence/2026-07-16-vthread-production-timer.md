# 2026-07-16 production virtual-thread timer evidence

Task: `T-P0-VTHREAD-TIMER`

## Result

The production virtual-thread scheduler owns `PccTimerHeap`, a binary min-heap
keyed by `(deadline, seq)` with an authoritative timer-id live map. The former
O(n) sorted linked-list insertion path is absent. Expiry is nondecreasing by
deadline, equal deadlines are FIFO, and the poller drains due timers in bounded
64-entry batches.

Cancellation is now part of the live scheduler route rather than only the
standalone oracle:

- every `PyVirtualThreadObject` has a non-GC backpointer to its one active
  timer node;
- `py_virtual_thread_cancel_timer()` removes the id from the heap live map
  under `pcc_vthread_lock`, unregisters the scheduler-root handle, clears the
  root slot, and recycles the stable node immediately;
- `complete`, `unpark`, `start`, and zero-delay rescheduling cancel an active
  timer before changing scheduler visibility;
- expiry clears the backpointer before transferring the thread from its timer
  root to exactly one ready-queue root;
- lazy stale heap tuples contain only opaque ids. The heap's `(deadline, seq)`
  validation makes immediate reuse of the same pooled node address safe.

The public runtime ABI and runtime-effect contract record timer cancellation as
a scheduler dequeue/root-leave operation. The relocating C and pcc-Python GC
mirrors clear the raw backpointer on an unqueued virtual-thread copy; queued
virtual threads remain non-relocatable until their registered scheduler slot is
updated, preserving the existing slot-based five-GC contract.

## Focused five-GC gates

The new production cancellation probe first failed because the backpointer and
cancel ABI did not exist. After the implementation it built one isolated
no-libpython C runtime and ran separate processes for GC0..4. Each backend
proved:

- explicit cancel is idempotent (`1`, then `0`) and drops timer count/root count
  immediately;
- the cancelled pooled address can be reused immediately while the old lazy
  heap tuple remains stale;
- the only retained reference can be the timer root across `gc.collect()`,
  then ownership transfers `timer root -> ready root -> no root`;
- completing a sleeping thread cancels it and produces no later wake;
- unparking a sleeping thread cancels the timer and enqueues exactly one wake.

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_vthread_timer_cancel.py

6 passed in 7.17s
```

The pre-existing production ordering gate rebuilt both `libpy_runtime.a` and
`libpy_runtime_pcc_py.a`, then ran their scheduler probes across GC0..4. It
proved out-of-order inserts wake in deadline order, FIFO for equal deadlines,
future-timer retention, and a 200-timer drain crossing the 64-entry batch.

```text
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_vthread_timer_heap_scheduler.py

2 passed in 44.64s
```

Focused continuation-root plus timer/IO/carrier regressions passed:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_virtual_thread_scheduler_roots.py \
  tests/python/test_gc_coroutine_scheduler_roots_production.py::test_virtual_thread_timer_poller_and_pinning_are_cooperative \
  tests/python/test_gc_coroutine_scheduler_roots_production.py::test_virtual_thread_carrier_run_loop_invokes_resume_pc

7 passed in 21.58s
```

Runtime-effect/virtual-thread wiring tests passed (`18 passed in 0.30s`), and
`py_compile` passed for the changed Python ABI/effect/test files. No bootstrap,
full-GC bootstrap matrix, or full test suite was run for this focused scheduler
card.

## Claim boundary

This proves production timer-heap integration, ordering, cancellation,
done-thread skip, immediate scheduler-root release, pooled-node reuse safety,
and pcc-Python archive compatibility under GC0..4. It does not prove the next
kqueue/epoll IO-waitset card, a timing-wheel implementation, one-million parked
virtual threads, or long-running RSS/pause/throughput performance at that
scale.
