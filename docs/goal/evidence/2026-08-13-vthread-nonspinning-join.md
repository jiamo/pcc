# Virtual-thread non-spinning join slice (2026-08-13)

Claim level: host frontend + C runtime, `ir_scaffold=on`, `libpython=off`.
This does not prove current-pcc1/self execution, the pcc-Python archive,
GC0..4 relocation/root balance, cancellation, or Tokio parity.

Implemented:

- `pcc.virtual_thread.join(task)` is a sequential `may_park` operation usable
  from a virtual thread. Host-thread use and self-join fail closed.
- A live target owns a FIFO of stable join nodes. Each node registers an
  updateable scheduler root for the waiter; the caller moves from `RUNNING` to
  `PARKED` without polling or blocking its carrier.
- Terminal publication transfers each join node directly into the ready queue.
  The handoff is allocation-free and preserves the same scheduler root until
  dequeue, avoiding an OOM lost-wakeup window.
- Multiple joiners receive the returned value. A `RAISED` outcome re-installs
  the stored exception so ordinary `try/except` handles it. Joining an already
  terminal task returns immediately and does not add a scheduling point.

Focused gate:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_virtual_thread_frontend.py::test_virtual_thread_join_parks_and_propagates_outcomes
```

Result: `1 passed in 0.87s`.

The single regression covers two concurrent joiners, exception propagation,
and immediate join. Its first scenario completes in exactly six scheduler
steps; the already-done join completes in one step.
