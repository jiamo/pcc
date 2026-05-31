# Investigation: Backend #4 tuple relocation must retain owned items

## Status
resolved

## Problem Description
Backend #4 list relocation now handles an out-of-line owned payload. Tuple is
the next smaller reference-bearing shape: its item slots live inline in the
same allocation. A plain relocation `memcpy` preserves the pointers but does not
give the moved tuple ownership of those child references. Releasing the old
forwarded tuple would then release children still referenced by the moved tuple.

## Repro
Run the focused C runtime and pcc-Python runtime mirror gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_tuple_copy_retains_owned_items' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_tuple_copy_retains_owned_items' -q -n0
```

Expected result after the fix: Backend #4 can select a tuple for relocation,
copy it, `py_incref()` the inline item references for the moved tuple, preserve
stable object identity, and keep the child alive after the root slot follows the
forwarding entry and releases the old tuple.

## Test [CONFIRMED]
The focused tests fail before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_tuple_copy_retains_owned_items' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_tuple_copy_retains_owned_items' -q -n0
```

Observed result:

```text
2 failed in 28.94s
stdout: 0
```

Both runtime archives build and run the probe, but Backend #4 does not yet
relocate tuple objects while preserving moved-tuple ownership of inline item
references.

## Proposals
- No.1 Add tuple-specific relocation item ownership     [CONFIRMED]

## No.1 Add tuple-specific relocation item ownership
### Code Change
Keep Backend #3 oldification support restricted to the already-supported scalar
tags. Add Backend #4-specific relocation support for `PY_TYPE_TUPLE`. After the
tuple allocation is copied, temporarily set the moved tuple length to zero for
failure-safe cleanup, validate the copied size covers all inline item slots,
`py_incref()` each item, then restore the tuple length.
### CONFIRMED
The focused C runtime and pcc-Python runtime mirror gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_tuple_copy_retains_owned_items' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_tuple_copy_retains_owned_items' -q -n0
```

Observed result:

```text
2 passed in 29.13s
```

The previously existing Task/queue forwarding gate and the new tuple gate pass
together:

```text
4 passed in 56.56s
```

The full relocation and abstraction gate passes:

```text
tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py:
28 passed in 96.61s
```

The Backend #3 non-list owned-slot gates still pass, confirming Backend #3
oldification support was not widened:

```text
2 passed in 29.77s
```

## Report (only when the investigation is closing)
No.1 landed. Backend #4 now has a second reference-bearing relocation case:
`PY_TYPE_TUPLE`. The moved tuple keeps its inline item slots but takes ownership
with `py_incref()` before any old forwarded source can be released. The
relocation step helper was made tolerant of the larger relocation support set by
waiting across bounded steps for an actual forwarding event instead of assuming
one tiny step always both selects and copies the newest scalar object.
