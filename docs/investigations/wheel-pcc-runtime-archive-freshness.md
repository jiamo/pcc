# Investigation: wheel runtime archive loses target and freshness evidence

## Status

resolved

## Problem Description

After wheel-installed `pcc1` found the packaged runtime directory, application
linking still failed. The wheel carried a valid `libpy_runtime_pcc_py.a`, but
not its target stamp. A later run also showed that ordinary make could leave a
pcc-emitted archive older than compiler/runtime inputs, so merely adding a
stamp would bless a stale artifact.

## Repro

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 -m integration \
  tests/integration/test_uv_pcc_project_environment.py
```

The first boundary emitted `failed to build py_runtime` followed by undefined
`pcc_gc_*` and `py_*` symbols. Inspection showed the installed archive itself
contained those symbols, while `<archive>.target` was absent.

## Test [CONFIRMED]

The integration test reproduced both the missing-stamp rebuild and the stale
archive rejection using a real built and installed wheel.

## Proposals

- No.1 Trust any archive found in the wheel [DENIED]
- No.2 Ship target evidence and force rebuild only when inputs are newer [CONFIRMED]

## No.1 Trust any archive found in the wheel

### Code Change

No change was made.

### DENIED

Skipping the target and source-freshness checks would allow cross-architecture
or compiler-stale archives to be published as valid.

## No.2 Ship target evidence and force rebuild only when inputs are newer

### Code Change

The wheel hook now installs the archive and `.target` stamp as one resource
contract. Before building, it compares the archive mtime with pcc compiler and
runtime `.py`, `.c`, `.h`, and Makefile inputs. It uses `make -B` only when an
input is newer; unchanged builds retain the fast make reuse path.

### CONFIRMED

The exact uv wheel integration gate passed: `1 passed in 6.86s`. The installed
compiler linked and ran a generic overlay package, then a recreated `.venv`
produced runtime `ImportError` rather than finding the deleted package in a
global site.

## Report

Wheel runtime artifacts are now target-labeled, freshness-checked build
outputs. Installed pcc1 accepts a current artifact directly and does not start
an unnecessary runtime rebuild in the user's environment.
