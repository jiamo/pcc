# Sequential virtual-thread channels focused evidence

Mode: host compiler, strict no-libpython executable, provenance-backed
pcc-Python runtime archive. This is not current-pcc1 or a five-GC claim.

Command:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_virtual_thread_channels.py::test_bounded_mpsc_oneshot_select2_sequential_contract
```

Result: `1 passed in 3.57s`.

The current-source canary proves bounded MPSC capacity/close behavior, oneshot
delivery and sender-close, left/right select2, loser unregistration, and
cooperative cancellation through the production pcc-Python runtime archive.

Still open: current-pcc1 self/no-libpython execution and the focused GC0..4
park/collect/root-balance product matrix.
