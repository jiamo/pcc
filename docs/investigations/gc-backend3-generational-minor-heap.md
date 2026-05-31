# Investigation: Backend 3 generational minor heap

## Status
resolved

## Problem Description
Implement the next Tier 5 GC backend productionization slice from `goal.md`:
backend #3 has young/old flags, remembered-set marking, and promotion-shaped
steps, but it does not have a domain-local bump-pointer minor heap allocator.

The first safe slice is intentionally narrow: prove that backend #3 can select
a small-object minor allocation path, account minor allocations/bytes, and
trigger a bounded minor collection/reset when the configured minor heap fills.
Full copy-into-old promotion remains separate because pcc's current refcount
deallocators directly `free(o)`, so returning slab pointers without a matching
free policy would be unsafe.

## Repro
Run the smallest new gate:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Expected before the fix: the new gate fails because backend #3 does not expose
minor allocation telemetry and does not trigger minor-heap collection/reset
when allocation pressure crosses the configured minor heap size.

## Test [CONFIRMED]
`tests/test_gc_backend_generational.py` is the focused gate for this slice.
The failing baseline was observed with:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
# 2 failed in 1.45s
```

Observed failures:

- backend #3 env selection and young flagging already work;
- `pcc_gc_telemetry(8)` and `pcc_gc_telemetry(10)` return `-1`, so there is no
  minor allocation / byte telemetry;
- repeated small allocations do not report any minor collection/reset.

## Proposals
- No.1 Add backend #3 minor-heap fast-path accounting and reset gate     [CONFIRMED]

## No.1 Add backend #3 minor-heap fast-path accounting and reset gate
### Code Change
The landed slice:

- add backend #3 env tuning for `PCC_GC_MINOR_HEAP_SIZE` and
  `PCC_GC_MINOR_ALLOC_MAX`;
- add minor allocation / collection / bytes telemetry counters;
- route qualifying backend #3 small allocations through a minor fast-path
  accounting hook without changing object pointer ownership yet;
- trigger a bounded minor reset/step when the configured minor heap fills;
- keep backend #0 behavior unchanged.

Touched files:

- `pcc/py_runtime/src/py_gc_backend.c`
- `pcc/py_runtime/include/py_runtime.h`
- `pcc/py_runtime/py/py_gc_backend.py`
- `pcc/py_runtime/py/py_substrate.py`
- `tests/test_gc_backend_generational.py`
- `tests/test_gc_abstraction_surface.py`

### CONFIRMED
The focused backend #3 gate now passes:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_generational.py tests/test_gc_abstraction_surface.py -q -n0
# 16 passed in 29.50s
```

Both runtime archives rebuild:

```bash
/opt/homebrew/bin/timeout 420s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" \
  make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
# success

/opt/homebrew/bin/timeout 180s env -u LC_ALL \
  make -B -C pcc/py_runtime libpy_runtime.a
# success; existing unused-function warnings remain for tracing helpers
```

The existing threading/GC substrate gate passes:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_threading_substrate.py -q -n0
# 12 passed in 2.52s
```

A backend #3 compatibility subset passes under env selection:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL PCC_GC_BACKEND=3 uv run pytest \
  tests/test_gc_backend_generational.py \
  'tests/test_gc_abstraction_surface.py::test_generational_gc_remembered_set_promotes_young_child' \
  tests/test_gc_effectiveness.py::test_non_default_backends_collect_list_cycle \
  tests/test_gc_effectiveness.py::test_non_default_backends_collect_cross_type_cycle \
  -q -n0
# 11 passed in 7.43s
```

## Report (only when the investigation is closing)
No.1 landed.  Backend #3 now has the first minor-heap productionization
surface: env-tuned small-allocation accounting, minor allocation / collection /
byte telemetry, and a pressure gate that proves reset behavior when the
configured minor heap fills.  This is deliberately not the full OCaml-style
minor heap: object memory still comes from the existing malloc/calloc ownership
path, because actual slab pointers require a follow-up deallocator/free policy
and root/remembered-set copy promotion.  Therefore `tasksV2.md` backend #3
must remain `partial`.
