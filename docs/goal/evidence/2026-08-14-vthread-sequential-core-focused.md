# Sequential virtual-thread core focused evidence

Mode: host compiler plus focused C/runtime contracts on Darwin. This is not a
current-pcc1, five-GC product matrix, or Rust Tokio compatibility claim.

Command:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_virtual_thread_frontend.py::test_virtual_thread_uncaught_exception_is_task_local \
  tests/python/test_virtual_thread_frontend.py::test_virtual_thread_join_parks_and_propagates_outcomes \
  tests/python/test_virtual_thread_frontend.py::test_virtual_thread_cancel_is_cooperative_and_runs_sync_cleanup \
  tests/python/test_virtual_thread_frontend.py::test_virtual_thread_sequential_readable_writable_use_platform_reactor \
  'tests/python/gc_production_contract/test_vthread_io_waitset_runtime.py::test_production_io_waitset_modes_preserve_roots[auto-0]'
```

Result: `5 passed in 11.95s`.

This proves the current-source narrow task-local failure, rooted FIFO join,
cooperative synchronous cancellation cleanup, sequential readable/writable
kqueue path, and Darwin auto/GC0 production waitset root contract.

Still open: current-pcc1 self/no-libpython execution, GC0..4 cancellation and
readiness product evidence, descriptor-close/reuse generation handling, and
the explicitly excluded async/multi-carrier/full-Tokio surfaces.
