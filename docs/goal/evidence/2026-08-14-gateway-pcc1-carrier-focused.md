# pcc-Python carrier parity — focused evidence

Date: 2026-08-14

Task: `GATEWAY-P2-PCC1-CARRIER-PARITY`

The source-contract and direct runtime-module compile nodes completed
fail-fast:

```text
2 passed, 6 deselected in 1.27s
```

The exact threaded pcc-Python archive integration node then built the current
content-addressed threaded runtime, linked its native C probe and executed the
persistent carrier path:

```text
gtimeout 600s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 \
  -m integration \
  tests/python/test_virtual_thread_pcc_py_carriers.py::test_threaded_pcc_python_archive_runs_persistent_carriers_and_pin_metrics

1 passed in 125.41s
```

The probe covers bounded carrier startup/stop, per-carrier queues, current
virtual-thread TLS, work completion, pin-reason metrics, parked waitset root
retention across stop/restart, readiness wake and final root cleanup.

The source-current pcc1 self/no-libpython GC0..4 application matrix remains
open until the separately owned HARNESS/compiler work reaches a stable source
identity. This is therefore `DONE_WEAK`, not current-pcc1 parity proof.
