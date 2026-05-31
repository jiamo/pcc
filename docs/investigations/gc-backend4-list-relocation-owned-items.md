# Investigation: Backend #4 list relocation must own item arrays

## Status
resolved

## Problem Description
Backend #4 is moving beyond scalar-object relocation. A list is a
reference-bearing container with an out-of-line owned `items` array. Relocating
it with a plain struct `memcpy` would leave the old and relocated list sharing
the same item array, causing double-free and lost child ownership once the old
forwarded source is released.

## Repro
Run the focused C runtime and pcc-Python runtime mirror gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_list_copy_owns_item_array' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_list_copy_owns_item_array' -q -n0
```

Expected result after the fix: Backend #4 can select a list for relocation,
copy it, give the moved list a distinct owned `items` array, preserve stable
object identity, and keep the child alive after the root slot follows the
forwarding entry and releases the old list.

## Test [CONFIRMED]
The focused tests fail before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_list_copy_owns_item_array' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_list_copy_owns_item_array' -q -n0
```

Observed result:

```text
2 failed in 27.73s
stdout: 0
```

Both runtime archives build and run the probe, but Backend #4 does not yet
relocate the list with an independently owned item array.

## Proposals
- No.1 Add list-specific relocation payload copy     [CONFIRMED]

## No.1 Add list-specific relocation payload copy
### Code Change
Keep Backend #3 oldification support restricted to the already-supported scalar
tags. Add Backend #4-specific relocation support for `PY_TYPE_LIST`. After the
list header is copied, allocate a fresh `items` array for the relocated list,
copy live item slots, and `py_incref()` each copied child so the moved list owns
its references independently of the forwarded source.
### CONFIRMED
The focused C runtime and pcc-Python runtime mirror gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_list_copy_owns_item_array' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_list_copy_owns_item_array' -q -n0
```

Observed result:

```text
2 passed in 27.72s
```

Broader relocation and abstraction gates also pass:

```text
tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py:
26 passed in 90.53s
```

The Backend #3 remembered-list slot gates still pass, confirming the Backend #3
oldification support set was not accidentally expanded:

```text
2 passed in 27.99s
```

## Report (only when the investigation is closing)
No.1 landed. Backend #4 has a first reference-bearing relocation case:
`PY_TYPE_LIST`. The colored-relocating support set now accepts lists, while the
Backend #3 oldification support set remains scalar-only. The relocated list
gets its own `items` array and owns its child references independently, so
healing a root slot from the forwarded source to the moved list can release the
old list without freeing the moved list's payload.
