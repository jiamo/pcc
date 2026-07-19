# 2026-07-05 backend4 zpage finalizer lifetime

Task: `AUD-P0-GC-SLOT-VISITOR`

Slice: fix the backend #4 red case exposed by the expanded ValueBox
property-return contract.

## Red

Focused node:

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  'tests/python/gc_production_contract/test_valuebox_roots.py::test_valuebox_pointer_payload_survives_gc[4]'
```

failed with the `return-any-old` finalizer event printed as `<null>` while
later method/static/class/property return cases finalized correctly.

Runtime log evidence under `PCC_GC_BACKEND=4 PCC_LOG=gc,refcount,finalizer`
showed the root lifetime bug: a `Track` user instance reported
`finalizer call tag=104 ptr=...`, then before finalizer completion the same
address was observed by refcount logging as tag `7`. The dying object span had
been made reusable before `__del__` finished reading `self.name`.

## Fix

Backend #4 zpage objects now delay `pcc_gc_note_object_freeing(...)` until
after type-specific dealloc/finalizer code has run. This preserves the dying
object header and fields while `__del__` and slot cleanup execute, without
disabling zpages or changing non-zpage dealloc ordering.

Touched runtime paths:

- `pcc/py_runtime/src/py_obj.c`: refcount-zero dealloc path delays the freeing
  note for backend #4 zpage objects until after `pcc_dealloc_dispatch`.
- `pcc/py_runtime/src/py_gc_backend.c`: tracing sweep PASS-2
  `pcc_gc_finalize_unreachable` mirrors the same delayed note.
- `pcc/py_runtime/py/py_obj.py`: pcc-Python refcount mirror.
- `pcc/py_runtime/py/py_gc_backend.py`: pcc-Python tracing sweep mirror.

## Gates

```bash
gtimeout 30s env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_gc_backend.py pcc/py_runtime/py/py_obj.py
```

passed.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  'tests/python/gc_production_contract/test_valuebox_roots.py::test_valuebox_pointer_payload_survives_gc[4]'
```

passed: `1 passed in 27.90s`.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valuebox_roots.py
```

passed: `5 passed in 1.43s`.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
```

passed: `5 passed in 1.28s`.

```bash
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract
```

passed: `140 passed in 31.84s`.

```bash
gtimeout 240s make -B -C pcc/py_runtime libpy_runtime.a
```

passed with existing warnings.

Additional attempted adjacent gate:

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_backend4_production.py
```

timed out after progress output and no final summary, so it is not counted as
green evidence.

## Boundary

No pcc1/pcc2/pcc3 bootstrap was run for this slice. `AUD-P0-GC-SLOT-VISITOR`
remains `DONE_WEAK`: broader value-payload slots, remaining pcc-Python mirror
parity outside covered families, future object-slot/value-payload families,
and current-source bootstrap proof remain open.
