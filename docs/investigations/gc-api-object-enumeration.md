# Investigation: native gc object enumeration should avoid libpython fallback

## Status
resolved

## Problem Description
The Phase G5 API tests for `gc.get_objects()` and `gc.get_referrers(obj)` still
fail before runtime because those calls fall through to libpython fallback.

The current target is the existing test contract:

- `gc.get_objects()` returns a native list containing tracked objects,
  including a self-referential list created after a stabilizing collection.
- `gc.get_referrers(obj)` returns a native list containing containers that
  directly reference `obj`.

This does not attempt CPython's full frame/temporary/referrer introspection.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_get_objects_finds_self_referential_list tests/test_gc_api.py::test_get_referrers_finds_holder -q -n0 --runxfail
```

Expected current failure before the fix:

```text
PyPipelineError: Python pipeline requires libpython fallback ... generated IR still calls py_cpy_* helpers
```

## Test [CONFIRMED]
The failing baseline was observed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_get_objects_finds_self_referential_list tests/test_gc_api.py::test_get_referrers_finds_holder -q -n0 --runxfail
```

Observed result: both tests failed at compile time with `py_cpy_*` fallback
requirements.

## Proposals
- No.1 Expose tracked-object and direct-referrer runtime helpers     [CONFIRMED]

## No.1 Expose tracked-object and direct-referrer runtime helpers
### Code Change
Add native runtime helpers for the API subset covered by the Phase G5 tests:

- `py_gc_get_objects()` walks the GC tracking list and appends every tracked
  object to a native list, using a pre-allocation tracking snapshot so the
  result list does not include itself.
- `py_gc_get_referrers(target)` walks tracked objects, visits each object's
  direct referents, and appends objects that directly reference `target` by
  pointer identity.
- The Python-port runtime exports the same helpers for
  `libpy_runtime_pcc_py.a`.
- The Python frontend lowers `gc.get_objects()` and `gc.get_referrers(obj)` to
  those helpers and allows both names through `from gc import ...`.
- Remove the stale xfails from the two object enumeration API tests.

This intentionally implements direct tracked-object enumeration only, not
CPython's complete stack/frame/temporary referrer introspection.

### CONFIRMED
The focused repro now passes without libpython fallback:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_get_objects_finds_self_referential_list tests/test_gc_api.py::test_get_referrers_finds_holder -q -n0 --runxfail
```

Observed result: `2 passed in 23.02s`.

The API suite now has both object enumeration tests unxfail'd:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_api.py -q -n0 -rxX
```

Observed result: `15 passed, 1 xfailed`.

The abstraction/native surface gate passed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_abstraction_surface.py tests/test_native_gc_module.py -q -n0
```

Observed result: `16 passed`.

Both runtime archive builds passed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s make -B -C pcc/py_runtime libpy_runtime.a
env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" /opt/homebrew/bin/timeout 300s make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
```

The full GC gate passed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `171 passed, 14 xfailed in 146.51s`.

## Report (only when the investigation is closing)
No.1 landed. The native API surface now covers `gc.get_objects()` and
`gc.get_referrers(obj)` for direct tracked heap objects without introducing
`py_cpy_*` fallback calls. This removes two Phase G5 API xfails while keeping
the implemented semantics deliberately narrower than CPython's full object
introspection behavior. The remaining API xfail is `gc.callbacks`, which is a
separate surface.
