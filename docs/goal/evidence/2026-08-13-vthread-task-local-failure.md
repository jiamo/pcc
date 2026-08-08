# Virtual-thread task-local failure slice (2026-08-13)

Claim level: host frontend + C runtime, `ir_scaffold=on`, `libpython=off`.
This does not prove current-pcc1/self execution, pcc-Python archive parity,
GC0..4 relocation, join/cancellation, Tokio parity, or Rust tokio crate
compatibility.

Implemented:

- Execution state remains backward-compatible `DONE`; a separate traced
  outcome records `PENDING`, `RETURNED`, `RAISED`, or future `CANCELLED`.
- An uncaught continuation exception is retained in the virtual-thread object
  before carrier TLS is cleared. The scheduler then continues unrelated ready
  work instead of returning a scheduler-wide error.
- The C and freestanding pcc-Python layouts both trace the exception slot;
  public `outcome(task)` and `exception(task)` accessors expose the result.

Focused gate:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_virtual_thread_frontend.py::test_virtual_thread_uncaught_exception_is_task_local
```

Result: `1 passed in 8.79s`.

The regression runs two generator-backed virtual threads on one carrier. One
raises after a yield and becomes `DONE/RAISED`; the other still completes as
`DONE/RETURNED` with result `42`. The successful process exit proves this
narrow host/C-runtime failure-isolation claim only.
