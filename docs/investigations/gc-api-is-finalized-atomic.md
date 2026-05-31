# Investigation: gc.is_finalized atomic values should not need libpython

## Status
resolved

## Problem Description
Phase G5 GC API has an xfail for `gc.is_finalized()` because the native gc
module surface does not expose it. The smallest safe slice is atomic values:
`gc.is_finalized(3)` and `gc.is_finalized(None)` should compile without
libpython fallback and return `False`.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_is_finalized_atomic_false -q -n0 --runxfail
```

Expected current failure before the fix: compilation requires libpython
fallback because generated IR still calls `py_cpy_*` helpers for
`gc.is_finalized`.

## Test [CONFIRMED]
The failing baseline was observed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_is_finalized_atomic_false -q -n0 --runxfail
```

Observed result:

```text
PyPipelineError: Python pipeline requires libpython fallback ... generated IR still calls py_cpy_* helpers
```

## Proposals
- No.1 Add native codegen for atomic `gc.is_finalized()`     [CONFIRMED]

## No.1 Add native codegen for atomic `gc.is_finalized()`
### Code Change
Add `is_finalized` to the native `gc` import allowlist and lower
`gc.is_finalized(obj)` to `False` for now. This exactly covers the existing
atomic false contract without pretending to expose finalized state for tracked
objects.
### CONFIRMED
Implemented in `pcc/py_frontend/pipeline.py` and
`pcc/py_frontend/codegen/layer1.py`, then removed the xfail marker from
`tests/test_gc_api.py::test_is_finalized_atomic_false`.

Verification:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_is_finalized_atomic_false -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_api.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_native_gc_module.py -q -n0
```

Observed results:

```text
1 passed
9 passed, 7 xfailed
2 passed
```

## Report
No.1 landed. `gc.is_finalized()` no longer falls back to `py_cpy_*` for the
atomic false contract covered by the existing Phase G5 test.

This is intentionally not a full finalized-state implementation for tracked
objects; it only exposes the CPython-compatible `False` result for values that
cannot have been finalized.
