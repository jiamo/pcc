# Investigation: Backend #1 gc.callbacks.remove does not stop later callbacks

## Status
resolved

## Problem Description
Under `PCC_GC_BACKEND=1`, `gc.callbacks.remove(cb)` does not prevent `cb`
from firing on a later explicit `gc.collect()`. The default backend already
passes the same API test, so this is a tracing-backend reachability or
callback-identity bug rather than the older libpython-fallback gap.

Related prior investigation: `docs/investigations/gc-api-callbacks.md`.

## Repro
Run:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_api.py::test_callbacks_fire_on_collect -q -n0 -rxX
```

Expected current failure before the fix:

```text
AssertionError: assert ['True', 'True', '4'] == ['True', 'True', '2']
```

The default backend should pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_callbacks_fire_on_collect -q -n0
```

## Test [CONFIRMED]
The failing Backend #1 baseline was observed with:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_api.py::test_callbacks_fire_on_collect -q -n0 -rxX
```

Observed result: the test failed because the final output was
`["True", "True", "4"]`, proving the callback fired again after removal.

The default-backend control was observed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_callbacks_fire_on_collect -q -n0
```

Observed result: `1 passed`.

## Proposals
- No.1 Probe whether callback list mutation survives Backend #1 collection     [CONFIRMED]
- No.2 Allocate pcc-Python function wrappers through the tracing allocator     [CONFIRMED]

## No.1 Probe whether callback list mutation survives Backend #1 collection
### Code Change
No source change. Runtime probes compared `len(gc.callbacks)` after
`append`, `collect`, and `remove`.

### CONFIRMED
The Backend #1 probe showed:

```text
len0 0
len1 1
len2 1 2
len3 1 2
len4 1 4
```

The default-backend control showed `len3 0` and `len4 0 2`. Removing
`gc.callbacks[0]` directly also worked under Backend #1, proving the list
mutation path was intact and the failed path was callback identity matching.

Additional probes showed that keeping `gc.get_referents(gc.callbacks[0])`
alive across the first collection made the later `remove(cb)` succeed. That
localized the failure to the function wrapper's captures tuple not being
traced from the callback list.

## No.2 Allocate pcc-Python function wrappers through the tracing allocator
### Code Change
Change `pcc/py_runtime/py/py_func.py` so the pcc-Python mirror of
`py_func_new` uses `pcc_gc_alloc(32, PY_TYPE_FUNC, 0)` instead of raw
`malloc(32)`, and so `py_dealloc_func` releases through
`pcc_gc_free_object_memory`.

This mirrors `pcc/py_runtime/src/py_func.c`. Native function wrappers now
enter Backend #1's object graph, so tracing a pinned `gc.callbacks` list can
mark the callback function and then its captures tuple.

### CONFIRMED
After rebuilding `libpy_runtime_pcc_py.a`, the failing test passed:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_api.py::test_callbacks_fire_on_collect -q -n0 -rxX
```

Observed result: `1 passed`.

Controls also passed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_api.py::test_callbacks_fire_on_collect -q -n0
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_api.py -q -n0 -rxX
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_abstraction_surface.py -q -n0
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_backend_incremental.py tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q -n0 -rxX
```

Observed results: `1 passed`, `16 passed`, `15 passed`, and `21 passed`.

## Report (only when the investigation is closing)
The landed fix is No.2. The root cause was not `gc.callbacks.remove` list
mutation. It was a mirror drift between C `py_func_new` and pcc-Python
`py_func_new`: the C implementation already used `pcc_gc_alloc`, while the
pcc-Python implementation allocated function wrappers with raw `malloc`.

Backend #1 traces only objects known through its allocation graph. A pinned
callbacks list could therefore keep the function wrapper alive by refcount,
but the tracing backend could not discover the wrapper's `captures` tuple.
After a collection, `remove(cb)` constructed a fresh wrapper and the
`entry/captures` comparison no longer matched the stored callback. Allocating
pcc-Python function wrappers through `pcc_gc_alloc` makes `PY_TYPE_FUNC`
referents visible to the tracing backend and restores callback identity.
