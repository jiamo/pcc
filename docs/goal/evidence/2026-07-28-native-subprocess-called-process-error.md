# Native subprocess CalledProcessError and pcc1 exit forwarding

Task: `BUG-P0-NATIVE-SUBPROCESS-CALLED-PROCESS-ERROR`

Status: `DONE_STRONG`

## Claim

In pcc-native Python mode with the self backend and no libpython,
`subprocess.run(..., check=True)` and `subprocess.check_call(...)` now raise
the pcc-Python `CalledProcessError` with usable `returncode`, `cmd`, `output`,
and `stderr` fields. A current-source pcc1 forwards a compiled child program's
nonzero exit status instead of crashing while reading `exc.returncode`.

This does not claim a full pcc1 -> pcc2 -> pcc3 fixed point or complete CPython
subprocess coverage.

## Root causes

1. Native subprocess lowering raised a generic runtime exception instead of
   instantiating the pcc-Python `CalledProcessError`.
2. The ordinary `system()` substrate returned encoded POSIX wait status
   directly (`exit 7` became 1792). The C substrate, pcc-Python substrate, and
   timeout path now share `py_process_normalize_wait_status`.

## Gates

- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_native_subprocess_no_libpython.py tests/python/test_native_subprocess_check_output.py`
  - `10 passed in 4.22s`
- `gtimeout 240s env -u LC_ALL uv run pytest -q -n0 tests/python/test_subprocess_timeout_runtime.py`
  - `3 passed in 3.89s`
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py`
  - `27 passed in 249.29s`
- `gtimeout 600s env -u LC_ALL uv run pytest -q -n0 tests/python/test_pcc1_python_smoke.py -k 'subprocess and returncode'`
  - final current-source run: `1 passed, 57 deselected in 138.32s`
- `cc -I pcc/py_runtime/include -fsyntax-only pcc/py_runtime/src/py_process_timeout.c pcc/py_runtime/src/py_process_substrate.c`
  - exit 0
