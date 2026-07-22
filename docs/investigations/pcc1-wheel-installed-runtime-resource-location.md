# Investigation: wheel-installed pcc1 loses its runtime resource root

## Status

resolved

## Problem Description

A native `pcc1` copied into a uv-managed virtual environment could report the
correct pcc package environment, but compiling a Python application failed
because the compiled frontend resolved its synthetic `__file__` relative to
the pytest working directory and looked for `<temp>/py_runtime`.

## Repro

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 -m integration \
  tests/integration/test_uv_pcc_project_environment.py
```

Before the repair, the application compile exited 1 with
`explicit runtime directory not found: <pytest-temp>/py_runtime`.

## Test [CONFIRMED]

The integration test reproduced the failure with a real wheel, uv-created
`.venv`, and wheel-installed `pcc1`. The focused prefix-discovery regression is
`test_native_pcc1_finds_runtime_resources_under_installed_prefix`.

## Proposals

- No.1 Require `PCC_SOURCE_ROOT` for wheel users [DENIED]
- No.2 Resolve installed resources from the environment/install prefix [CONFIRMED]

## No.1 Require `PCC_SOURCE_ROOT` for wheel users

### Code Change

No change was made.

### DENIED

This would expose a repository-only implementation detail in the normal user
workflow and would not work after installing only the wheel.

## No.2 Resolve installed resources from the environment/install prefix

### Code Change

The compiled frontend now searches conventional `site-packages/pcc` layouts
below `VIRTUAL_ENV` and below the prefix containing `bin/pcc1`. Candidates
must still satisfy the existing pcc source/resource shape check. Nonexistent
`lib`/`lib64` roots are skipped before listing so native directory probes do
not leak shell diagnostics.

### CONFIRMED

The exact uv wheel integration gate passed after rebuilding stage1:
`1 passed in 6.86s`.

## Report

No user path variable is required. Source-tree overrides remain supported for
development, while an installed pcc1 finds the runtime shipped in the same
virtual environment.
