# Fallback action classifier focused evidence (2026-08-14)

Mode: host pcc, compiler-only focused probes. This evidence does not claim the
full stage1 closure or sequential pcc1 fixed point.

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_fallback_baseline.py::test_standalone_fallback_metric_rejects_unknown_bridge_symbols \
  tests/python/test_fallback_baseline.py::test_pipeline_subprocess_run_kwargs_resolve_without_cpython_bridge
2 passed in 0.12s

gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_fallback_baseline.py::test_standalone_fallback_metric_counts_actions_not_ownership_plumbing \
  tests/python/test_fallback_baseline.py::test_standalone_action_ratchet_defers_codegen_failures_to_pass_count
2 passed in 0.08s
```

The small subprocess keyword-call canary lowers through the native process
ABI with no CPython bridge. Standalone metrics count semantic actions rather
than conversion/refcount plumbing, and an unknown `py_cpy_*` symbol is a hard
failure. The two full fallback suites and their exact-zero linked closures
remain final frozen-source gates.
