# Investigation: backend 3 pcc-Python minor refill promotion parity

## Status
resolved

## Problem Description
Backend #3 has a C-runtime gate proving that minor-heap refill promotes a
remembered old-to-young child. The pcc-Python runtime-high path now has minor
arena allocation and constructor-preserved young/minor flags, but it does not
yet have a matching pcc-Python runtime-high gate for refill-time promotion.

Reduced target: under `PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py`, an old owner
storing a young child should be remembered, and subsequent minor arena pressure
should trigger a minor collection that promotes the young child to old and
clears the owner's remembered flag.

## Repro
Run the focused pcc-Python runtime-high regression:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_promotes_remembered_young_child \
  -q -n0
```

## Test [N/A]
This investigation added parity coverage rather than confirming a production
code failure. The first probe version double-freed the manually allocated list
items array in the test itself. After removing that probe bug, the pcc-Python
runtime-high path already satisfied the C-runtime behavior.

Focused regression:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_promotes_remembered_young_child \
  -q -n0
```

Observed result: `1 passed in 0.76s`.

## Proposals
- No.1 Add pcc-Python minor-refill remembered-set promotion coverage     [CONFIRMED]

## No.1 Add pcc-Python minor-refill remembered-set promotion coverage
### Code Change
Added
`tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_promotes_remembered_young_child`.

The probe runs under `PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py` and backend #3.
It:

- allocates an owner list-shaped object;
- promotes the owner to old with `pcc_gc_step(1)`;
- stores a young child through `pcc_gc_store_ptr()`;
- creates enough minor arena pressure to trigger a refill-time minor
  collection;
- verifies the child becomes old and the owner's remembered flag is cleared.

### CONFIRMED
Focused regression:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_promotes_remembered_young_child \
  -q -n0
```

Observed result: `1 passed in 0.76s`.

Backend #3 focused file:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 600s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Observed result: `8 passed in 9.64s`.

## Report (only when the investigation is closing)
No production code change was needed. Existing backend #3 pcc-Python
runtime-high logic already promotes remembered young children during minor
refill. The missing piece was executable parity coverage matching the existing
C-runtime minor-refill gate.
