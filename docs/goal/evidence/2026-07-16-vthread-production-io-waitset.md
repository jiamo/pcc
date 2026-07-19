# 2026-07-16 production virtual-thread IO-waitset evidence

Task: `T-P0-VTHREAD-KQUEUE`

## Result

The production scheduler in `pcc/py_runtime/src/pcc_threads.c` now owns the
previously standalone `PccIoWaitSet`:

- Darwin/BSD `auto` mode selects the real `kqueue`/`kevent(2)` backend.
- `PCC_VTHREAD_IO_BACKEND=poll` forces the portable production fallback; a
  platform without kqueue also selects this fallback.
- `py_virtual_thread_io_backend()` exposes the selected mode as
  `PCC_VTHREAD_IO_BACKEND_KQUEUE` or `PCC_VTHREAD_IO_BACKEND_POLL`. Selection is
  process-stable after first scheduler IO initialization.
- The fallback makes one `poll(2)` call over the waitset's unique live fds and
  feeds the common level-triggered drain. The former one-poll-syscall for every
  linked vthread entry is absent from `py_virtual_thread_poll_io()`.

Stable pooled `PccVirtualThreadPollEntry` nodes continue to own per-vthread GC
roots. The kernel waitset aggregates the interest-mask union and earliest
deadline for each fd; it does not collapse same-fd vthreads into one semantic
waiter. When an fd fires, each matching entry applies its own interest/deadline,
then the aggregate registration is rearmed only if later or differently
interested waiters remain.

`PyVirtualThreadObject.io_entry` provides an internal non-GC cancellation
backpointer. Complete, unpark, start, sleep, or switching to another fd wait
removes the old wait node, unregisters its root handle, clears the root slot,
and refreshes the shared-fd registration immediately. Readiness/timeout clears
the backpointer before transferring ownership from the IO root to exactly one
ready-queue root. Unqueued relocating copies clear both raw scheduler
backpointers in the C and pcc-Python GC mirrors.

## Focused gates

The new production probe built one isolated no-libpython C runtime, then ran
both `auto` and forced-`poll` modes in separate processes for GC0..4. On this
Darwin host `auto` was asserted to be kqueue. Every mode/backend combination
covered:

- live pipe readability;
- live Unix `socketpair` readability;
- inclusive deadline timeout without readiness;
- two independently rooted vthreads waiting on the same fd;
- `gc.collect()` while the only scheduler ownership is the IO root;
- root transfer `IO -> ready -> none`;
- early completion cancellation and no later stale wake;
- bounded IO-node reuse.

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_vthread_io_waitset_runtime.py

11 passed in 7.45s
```

The prior timer/IO node-pool, scheduler-root, timer-cancel, and standalone
waitset oracle/mirror gates remained green together:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_vthread_timer_io_node_pool.py \
  tests/python/gc_production_contract/test_virtual_thread_scheduler_roots.py \
  tests/python/gc_production_contract/test_vthread_timer_cancel.py \
  tests/vthread/test_io_waitset_mirror.py

29 passed in 21.56s
```

The pcc-Python archive rebuilt and exercised ready/timer/IO continuation roots
through the same shared C scheduler/waitset seam across GC0..4:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_coroutine_roots.py::test_pcc_python_runtime_virtual_thread_scheduler_queues_keep_continuation_roots_across_backends

1 passed in 35.76s
```

Virtual-thread ABI/effect baselines passed (`18 passed in 0.26s`), and
`py_compile` passed for the changed Python ABI/GC-mirror/test files. No
bootstrap, full-GC bootstrap matrix, or broad test suite was run.

## Claim boundary

This proves that the Darwin production scheduler owns real kqueue and that the
portable production route owns an explicit one-call live-poll fallback, with
pipe/socket/timeout/same-fd/cancel/root semantics under GC0..4 and the
pcc-Python archive boundary. It does not prove Linux epoll, an O(ready)
per-vthread dispatch index (the current scheduler still matches delivered fds
against its rooted entry list), one-million parked virtual threads, or
long-running RSS/pause/throughput behavior. Those remain later M4 cards.
