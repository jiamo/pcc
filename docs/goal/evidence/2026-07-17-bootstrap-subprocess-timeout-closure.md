# AUD-P1-BOOTSTRAP-SUBPROCESS-TIMEOUTS closure evidence

All 88 `subprocess.run(..., check=True)` sites in `cli_bootstrap.py` now use
one `_bootstrap_subprocess_run` boundary. It enforces 300 seconds by default,
configurable with `PCC_BOOTSTRAP_SUBPROCESS_TIMEOUT_SECONDS`; only the wrapper's
single underlying `subprocess.run` remains.

The pcc1 path is genuinely enforced rather than mode-labeled only:

- `py_subprocess_run_timeout` uses `posix_spawnp`, a distinct child process
  group, monotonic `waitpid(WNOHANG)` polling, then group `SIGTERM`/grace/
  `SIGKILL` cleanup.
- The C and pcc-Python runtime archives consume that same C-kernel primitive;
  both archives contain exactly one exported timeout symbol.
- Native subprocess lowering no longer accepts and discards `timeout=`. It
  emits `py_subprocess_run_timeout` and distinguishes timeout return `-124`.
- `pcc.py_stdlib.subprocess.run` supports positive integer timeouts and raises
  `TimeoutExpired` for the same sentinel.

Focused evidence:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/python/test_subprocess_timeout_runtime.py::test_native_subprocess_timeout_kills_child_process_group
1 passed in 35.29s

gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/python/test_cli_bootstrap_observability.py tests/python/test_native_subprocess_check_output.py
25 passed in 2.02s

gtimeout 120s env -u LC_ALL PCC_DEBUG_STRICT_NOLIB_STUB=_bootstrap_subprocess_run uv run pcc --backend self --python-libpython=off --ir-scaffold=on pcc/__main__.py -o build/bootstrap-timeout-pcc1/pcc1
exit 0 in about 58s; no strict-stub diagnostic

gtimeout 30s env -u LC_ALL PCC_CURRENT_PCC1=build/bootstrap-timeout-pcc1/pcc1 uv run pytest -q -n0 tests/python/test_subprocess_timeout_runtime.py::test_pcc1_bootstrap_wrapper_enforces_timeout
1 passed in 1.55s
```

Both native gates start a five-second shell/grandchild workload, enforce a
one-second deadline, and verify that a post-sleep marker is never written.
No full bootstrap, GC matrix, or full GCC suite was run.
