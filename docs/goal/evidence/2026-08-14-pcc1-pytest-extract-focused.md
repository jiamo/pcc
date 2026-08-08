# PCC1 pytest extraction focused evidence — 2026-08-14

Mode: host-side source/facade/closure contracts.

Command:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_cli_bootstrap_pytest_extract.py
```

Result: 5 passed. The extracted launcher delegates through a thin facade,
retains the expected harness behavior and dependency ordering, uses the
closure-safe import shape, and leaves none of the old embedded implementation
family in `cli_bootstrap.py`.

Current-pcc1 `--pytest`, bootstrap-baseline and sequential fixed-point
execution remain open.
