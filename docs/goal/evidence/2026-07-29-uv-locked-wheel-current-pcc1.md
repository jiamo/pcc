# uv-locked wheel current-source pcc1 evidence

Date: 2026-07-29

Task: `PKG-P1-UV-LOCKED-NATIVE-SYNC`

## Boundary

The reported real NumPy path completed locked synchronization and selected the
uv-owned pcc overlay, but the wheel-installed pcc1 crashed during its
locked-environment restart with:

```text
AttributeError: returncode
```

Current native subprocess lowering was not the failing source boundary. After
the repository pcc1 had been rebuilt, the fixed
`build/bootstrap/pcc1` forwarded exit 7 from a minimal program, and the exact
NumPy node passed unchanged in `283.58s`.

The integration wheel fixture nevertheless selected that fixed path directly.
It could therefore put a stale pcc1 into a newly built wheel after frontend or
runtime source changed. The fixture now uses the shared
`find_current_pcc1(REPO)` freshness/provisioning contract and fails closed when
no current compiler is available.

This is host-provisioned pcc1, self backend, no-libpython package integration
evidence. It is not pcc1-to-pcc2-to-pcc3 fixed-point evidence.

## RED

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_test_infrastructure_efficiency.py::test_uv_locked_wheel_bundles_a_current_source_pcc1

assert "find_current_pcc1(REPO)" in source
1 failed in 0.05s
```

## GREEN

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_test_infrastructure_efficiency.py
21 passed in 0.67s

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_pcc1_gate.py
6 passed in 0.08s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_subprocess_no_libpython.py \
  -k preserves_called_process_error_fields
1 passed, 1 deselected in 1.19s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_package_uv_lock_sync.py
7 passed in 0.30s

gtimeout 900s env -u LC_ALL uv run pytest -q -n0 -m integration \
  tests/integration/test_uv_locked_pcc_sync.py
2 passed in 290.42s
```

## Open evidence

The task remains `IN_PROGRESS`: this slice does not supply final summaries for
the exact default six-worker non-integration suite or the complete integration
suite.
