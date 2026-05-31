# Investigation: passive gc API surfaces should avoid libpython fallback

## Status
resolved

## Problem Description
Two Phase G5 API tests still fail before runtime because the native `gc`
surface does not lower passive observation APIs:

- `gc.garbage`
- `gc.get_stats()`

Both should compile without `py_cpy_*` fallback for the limited contracts
currently tested.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_garbage_starts_empty tests/test_gc_api.py::test_get_stats_has_required_keys -q -n0 --runxfail
```

Expected current failure before the fix:

```text
PyPipelineError: Python pipeline requires libpython fallback ... generated IR still calls py_cpy_* helpers
```

## Test [CONFIRMED]
The failing baseline was observed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_garbage_starts_empty tests/test_gc_api.py::test_get_stats_has_required_keys -q -n0 --runxfail
```

Observed result: both tests failed at compile time with `py_cpy_*` fallback
requirements.

## Proposals
- No.1 Lower `gc.garbage` and `gc.get_stats()` natively for current contracts     [CONFIRMED]

## No.1 Lower `gc.garbage` and `gc.get_stats()` natively for current contracts
### Code Change
Teach the Python pipeline and layer1 native-builtin import handling that
`gc.get_stats` is a native `gc` import-from name.

Lower `gc.get_stats()` to a native list containing one dict with the required
minimum keys: `collections`, `collected`, and `uncollectable`. The values are
currently zero counters because this gate only requires the passive CPython
shape without libpython fallback.

Lower `gc.garbage` attribute reads on the native `gc` module to a fresh empty
native list. This implements the current tested passive contract, including the
modern "normal finalizer cycles do not populate garbage" expectation; it is not
yet CPython's mutable module-level uncollectable list.

Remove stale xfail markers for:

- `tests/test_gc_api.py::test_get_stats_has_required_keys`
- `tests/test_gc_api.py::test_garbage_starts_empty`
- `tests/test_gc_finalizer_corner.py::test_gc_garbage_populated_for_uncollectable`

### CONFIRMED
The focused fallback repro now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_garbage_starts_empty tests/test_gc_api.py::test_get_stats_has_required_keys -q -n0 --runxfail
```

Observed result: `2 passed`.

The focused API and native-module gates pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_api.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_native_gc_module.py -q -n0
```

Observed results: `11 passed, 5 xfailed` and `2 passed`.

The full GC gate initially passed behaviorally but reported one stale XPASS:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result before removing the final stale xfail:
`166 passed, 18 xfailed, 1 xpassed`; the XPASS was
`tests/test_gc_finalizer_corner.py::test_gc_garbage_populated_for_uncollectable`.

After removing that stale marker, the full GC gate passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `167 passed, 18 xfailed`.

## Report (only when the investigation is closing)
No.1 landed because it covers the currently tested passive `gc` API surface
without reintroducing `py_cpy_*` fallback. The implementation is intentionally
minimal: `gc.get_stats()` exposes the CPython-compatible container/key shape
with zero counters, and `gc.garbage` is a fresh empty list rather than a mutable
module-level uncollectable list. The richer telemetry and persistent garbage
list semantics should be tracked separately if future tests require them.
