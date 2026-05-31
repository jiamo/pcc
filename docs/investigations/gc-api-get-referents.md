# Investigation: native gc.get_referents should avoid libpython fallback

## Status
resolved

## Problem Description
The Phase G5 API test for `gc.get_referents(obj)` still fails before runtime
because the native `gc` module does not lower this object-graph observation
API. The current target is the existing test contract: for a native container,
return a list containing its outgoing references.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_get_referents_returns_outgoing -q -n0 --runxfail
```

Expected current failure before the fix:

```text
PyPipelineError: Python pipeline requires libpython fallback ... generated IR still calls py_cpy_* helpers
```

## Test [CONFIRMED]
The failing baseline was observed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_get_referents_returns_outgoing -q -n0 --runxfail
```

Observed result: the test failed at compile time with `py_cpy_*` fallback
requirements.

## Proposals
- No.1 Expose a native referents-list runtime helper     [CONFIRMED]

## No.1 Expose a native referents-list runtime helper
### Code Change
Add `py_gc_get_referents(PyObject *)` to the runtime ABI, C runtime header, C
runtime, and pcc-Python runtime port. The C implementation reuses the existing
`py_gc_visit_referents` traversal and appends each outgoing child to a native
list. The pcc-Python port mirrors the same container cases so the runtime
archive can still be built from the Python sources.

Teach native `gc` import-from and layer1 call-lowering allowlists about
`get_referents`, and lower `gc.get_referents(obj)` to the native runtime helper.

Remove the stale xfail marker from
`tests/test_gc_api.py::test_get_referents_returns_outgoing`.

### CONFIRMED
The focused fallback repro now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_get_referents_returns_outgoing -q -n0 --runxfail
```

Observed result: `1 passed`.

The focused API/runtime ABI gates pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_api.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_abstraction_surface.py tests/test_native_gc_module.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s make -B -C pcc/py_runtime libpy_runtime.a
```

Observed results: `13 passed, 3 xfailed`; `16 passed`; C runtime archive
built successfully.

The full GC gate passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `169 passed, 16 xfailed`.

The pcc-built runtime archive also passes:

```bash
env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" /opt/homebrew/bin/timeout 300s make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
```

Observed result: archive built successfully.

## Report (only when the investigation is closing)
No.1 landed because it covers the current outgoing-referents contract without
needing the broader object-list/referrer enumeration APIs. The implementation
returns a native list of direct referents for built-in containers and runtime
objects already covered by the GC visitor. `gc.get_objects()` and
`gc.get_referrers()` remain separate Phase G5 work.
