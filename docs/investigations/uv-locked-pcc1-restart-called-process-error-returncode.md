# Investigation: uv-locked pcc1 restart loses CalledProcessError returncode

## Status

resolved

## Problem Description

The real locked NumPy integration completes both `pcc sync --locked` passes and
selects the uv-owned pcc overlay, but the subsequent pcc1 application compile
fails in `_restart_with_locked_environment_defaults`. The compiled pcc1 catches
a native subprocess failure as `subprocess.CalledProcessError`, then reading
`exc.returncode` raises:

```text
AttributeError: returncode
```

This is a regression against the resolved current-source native subprocess
proof in
[`native-subprocess-called-process-error-returncode.md`](native-subprocess-called-process-error-returncode.md).
It may be a distinct compiled closure, restart, or installed-wheel boundary;
the existing resolved investigation must not be silently reopened or rewritten.

## Repro

```bash
gtimeout 900s env -u LC_ALL uv run pytest -q -n0 -m integration \
  tests/integration/test_uv_locked_pcc_sync.py::test_uv_locked_numpy_sync_compiles_and_runs_without_libpython
```

Expected: the uv-owned pcc1 compiles and runs `numpy_app.py` without libpython,
while preserving `uv.lock` and reusing the unchanged native environment.

Observed from the user report: the compile subprocess exits 1 with
`AttributeError: returncode` at `pcc/cli_bootstrap.py:109`.

## Test [CONFIRMED]

The fixed `build/bootstrap/pcc1` available after the report correctly forwarded
exit 7 from a minimal current-source probe, and the exact NumPy integration
passed unchanged with that current artifact:

```text
1 passed in 283.58s
```

That ruled out a current native-lowering regression. The wheel fixture instead
hard-coded `build/bootstrap/pcc1` without applying the repository's freshness
gate. The deterministic infrastructure regression failed before the proposal:

```text
assert "find_current_pcc1(REPO)" in source
1 failed in 0.05s
```

Thus the fixture could bundle an older compiler that retained the fieldless
native exception behavior from before the resolved subprocess fix.

## Proposals

- No.1 Repair the first native subprocess exception producer on the locked
  pcc1 restart path [DENIED]
- No.2 Hide the missing field with `getattr(exc, "returncode", 1)` [DENIED]
- No.3 Disable locked-environment restart for compiled pcc1 [DENIED]
- No.4 Require a current-source pcc1 before building the integration wheel
  [CONFIRMED]

## No.1 Repair the first native subprocess exception producer on the locked
pcc1 restart path

### Code Change

None.

### DENIED

The current fixed pcc1 forwarded exit 7 and compiled the exact locked NumPy
application successfully. Changing shared lowering would not address the
fixture's ability to package an older binary.

## No.2 Hide the missing field with `getattr(exc, "returncode", 1)`

### Code Change

None.

### DENIED

That would turn a broken exception object into a generic exit code and regress
the already-established native subprocess semantics.

## No.3 Disable locked-environment restart for compiled pcc1

### Code Change

None.

### DENIED

The restart is the boundary that selects the deterministic uv-owned pcc
environment. Bypassing it would make this package gate green by weakening the
environment contract.

## No.4 Require a current-source pcc1 before building the integration wheel

### Code Change

Use the existing `tests.python.pcc1_gate.find_current_pcc1` freshness and
provisioning contract in the locked-sync wheel fixture. Fail when no
current-source compiler can be produced, and pass the verified path through
`PCC_BUILD_PCC1`; never fall back to the unvalidated fixed build path.

### CONFIRMED

The deterministic infrastructure contract turned GREEN, the complete
infrastructure and freshness suites passed, and the current native subprocess
field regression remained GREEN:

```text
21 passed in 0.67s
6 passed in 0.08s
1 passed, 1 deselected in 1.19s
```

The complete locked-sync integration file then built one verified wheel and
passed both the generic dependency graph and real NumPy compile/run paths:

```text
2 passed in 290.42s
```

## Report

Resolved by Proposal No.4. The native `CalledProcessError` implementation was
already correct in the current pcc1; the integration fixture allowed an older
fixed-path compiler to enter a newly built wheel. Wheel construction now
enforces current-source pcc1 identity through the shared freshness gate.
Proposals that hid the field, bypassed environment restart, or changed current
lowering were denied because each would weaken semantics or patch the wrong
boundary.
