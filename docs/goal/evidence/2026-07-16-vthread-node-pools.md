# 2026-07-16 virtual-thread node-pool evidence

Task: `T-P0-VTHREAD-NODE-POOL`

## Result

The production C runtime now uses four separately bounded virtual-thread node
pools under the synchronization contract that already owns each queue:

| Family | Pool limit | Synchronization | Root-slot lifecycle |
|---|---:|---|---|
| ready | 4096 | `pcc_vthread_lock` | register on enqueue; unregister/clear before recycle |
| waiter | 4096 | dedicated waiter-pool mutex | register on park; unregister/clear before recycle |
| timer | 4096 | `pcc_vthread_lock` | register before heap insert; unregister/clear after heap removal |
| IO | 4096 | `pcc_vthread_lock` | register before waitset link; unregister/clear after unlink |

The existing ready-entry and lock/event/condition/semaphore waiter pools were
retained.  Ready entries now use the pool on carrier queues as well as the
single global ready queue.  Timer nodes no longer use one `calloc/free` pair per
sleep; their stable address remains the timer-heap ID only until that ID is
popped, after which the root handle is unregistered, the root slot is cleared,
and the node may be recycled.  IO wait nodes follow the analogous unlink then
release/recycle order.  Registration-failure paths recycle only cleared nodes;
heap-insertion failure uses the full registered-root release path.

`py_virtual_thread_node_pool_stat(family, metric)` exposes physical
allocations, freelist reuses, and current cached nodes.  The statistic is part
of the public runtime ABI.  Ready/timer/IO statistics are read under the
scheduler lock.  Waiter nodes are implemented in `py_threading.c`, but their
counters live in the always-present `pcc_threads.c` C kernel and are updated
through narrow atomic note functions.  This avoids making the C kernel depend
on `py_threading.c`, which is replaced by `py/py_threading.py` in the
pcc-Python archive.  The pcc-Python archive therefore links cleanly; its
simplified threading mirror creates no C waiter nodes and leaves those counts
at zero.

## Five-GC allocation and root measurement

The timer/IO probe runs 16 timer parks and 16 IO timeout parks per backend.  It
releases the caller reference, triggers collection while the only live
continuation reference is the scheduler-rooted node slot, wakes the thread,
and verifies the root count changes `1 -> 1 -> 0` across park, queue transfer,
and dequeue.  The measured reuse counts were identical for GC0..4:

```text
backend:ok:ready_reuses:timer_reuses:io_reuses
0:1:31:15:15
1:1:31:15:15
2:1:31:15:15
3:1:31:15:15
4:1:31:15:15
```

The waiter probe runs eight rounds with 64 contending virtual threads.  For
each GC backend it measured 63 physical waiter-node allocations, 441 reuses,
and 63 cached nodes after all queues and scheduler roots drained:

```text
backend:semantic_ok:drained:reused:allocations:reuses:cached
0:1:1:1:63:441:63
1:1:1:1:63:441:63
2:1:1:1:63:441:63
3:1:1:1:63:441:63
4:1:1:1:63:441:63
```

## Gates

Final waiter/timer/IO five-backend measurement build:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  --basetemp=/tmp/pcc_vthread_pool_measure \
  tests/python/gc_production_contract/test_vthread_waiter_node_pool.py \
  tests/python/gc_production_contract/test_vthread_timer_io_node_pool.py

10 passed in 13.94s
```

Final ready source-shape and five-backend runtime root/reuse gate:

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_virtual_thread_ready_entry_pool.py

2 passed in 7.03s
```

The combined four-family gate plus the `PCC_WITH_THREADS=1` persistent carrier
path passed before the C-kernel statistic ownership cleanup:

```text
13 passed in 27.63s
```

That cleanup was then covered by the final 10+2 tests above.  The focused
pcc-Python archive queue-root gate rebuilt, linked, and ran ready/timer/IO
continuation roots across GC0..4:

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_coroutine_roots.py::test_pcc_python_runtime_virtual_thread_scheduler_queues_keep_continuation_roots_across_backends

1 passed in 35.73s
```

The public header/runtime-ABI wiring test passed in 0.26s.  A complete
`libpy_runtime_pcc_py.a` rebuild reached the final `ar`, `ranlib`, and atomic
archive replacement with exit 0.  `py_compile` passed for the changed Python
test/ABI files.  No full bootstrap or full-GC bootstrap matrix was run for this
node-allocation-only card.

## Claim boundary

This proves bounded allocation reuse and scheduler-root lifecycle preservation
for ready, waiter, timer, and current poll-fallback IO nodes under GC0..4,
including the threaded carrier build and the pcc-Python archive link boundary.
It does not claim timer cancellation (the next timer card), kqueue integration,
runtime-effect event completeness, one-million virtual-thread readiness, or
RSS/latency/GC-pause performance at that scale.
