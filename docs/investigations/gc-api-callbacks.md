# Investigation: native gc callbacks should avoid libpython fallback

## Status
active

## Problem Description
The Phase G5 API test for `gc.callbacks` is still xfailed. The target behavior
is the existing test contract: callbacks appended to `gc.callbacks` fire with
`phase == "start"` and `phase == "stop"` around an explicit `gc.collect()`, and
can be removed afterward.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_callbacks_fire_on_collect -q -n0 --runxfail
```

Expected current failure before the fix:

```text
PyPipelineError: Python pipeline requires libpython fallback ... generated IR still calls py_cpy_* helpers
```

## Test [CONFIRMED]
The failing baseline was observed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_callbacks_fire_on_collect -q -n0 --runxfail
```

Observed result: the test failed at compile time with `py_cpy_*` fallback
requirements.

## Proposals
- No.1 Expose a native gc.callbacks list and fire it in pcc_gc_collect     [pending]

## No.1 Expose a native gc.callbacks list and fire it in pcc_gc_collect
### Code Change
pending
### CONFIRMED|DENIED|DENIED BY USER
pending
