# Investigation: Backend #4 instance relocation must retain owned fields

## Status
resolved

## Problem Description
Backend #4 now relocates scalar objects, lists, tuples, dicts, sets, and Tasks.
A Python instance owns its declared field slots and, when the class is not
slots-only, a hidden dynamic-attribute dict slot at `fields[n_fields]`. Its
`cls` pointer is borrowed metadata and must stay pointer-identical, but must
not be treated as an owned slot. A plain relocation copy would copy field
pointers without giving the moved instance ownership. Releasing the old
forwarded instance could then drop the field value or dynamic dict still used
by the moved instance.

## Repro
Run the focused C runtime and pcc-Python runtime mirror gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_instance_copy_retains_owned_fields' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_instance_copy_retains_owned_fields' -q -n0
```

Expected result after the fix: Backend #4 can select a user-tagged instance
for relocation, preserve its borrowed class pointer, `py_incref()` the declared
field and dynamic-attribute dict slots for the moved instance, and keep field
lookup, dynamic attribute lookup, and `isinstance` correct after the root slot
follows the forwarding entry and releases the old instance.

## Test [CONFIRMED]
The focused tests fail before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_instance_copy_retains_owned_fields' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_instance_copy_retains_owned_fields' -q -n0
```

Observed result:

```text
2 failed in 27.77s
stdout: 0
```

Both runtime archives build and run the probe, but Backend #4 does not yet
relocate instances while preserving moved-instance ownership of declared field
slots and the dynamic-attribute dict slot.

## Proposals
- No.1 Add instance-specific relocation field ownership     [CONFIRMED]

## No.1 Add instance-specific relocation field ownership
### Code Change
Add Backend #4-specific relocation support for `PY_TYPE_INSTANCE` and
user-defined instance tags. Neutralize the copied instance's `cls` pointer
before validation so failure cleanup cannot decref borrowed field pointers,
then restore `cls`, copy each owned field slot, and `py_incref()` every
non-null owned slot.
### CONFIRMED
The focused C runtime and pcc-Python runtime mirror gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_instance_copy_retains_owned_fields' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_instance_copy_retains_owned_fields' -q -n0
```

Observed result:

```text
2 passed in 27.95s
```

The full relocation and abstraction gate passes:

```text
tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py:
36 passed in 229.52s
```

The Backend #3 non-list owned-slot gates still pass:

```text
2 passed in 28.73s
```

## Report (only when the investigation is closing)
No.1 landed. Backend #4 now relocates `PY_TYPE_INSTANCE` and user-defined
instance tags while preserving borrowed class metadata and independently
owning declared fields plus the dynamic-attribute dict slot. The payload copy
neutralizes `cls` during validation so failure cleanup cannot release copied
field pointers that the moved instance has not yet claimed.
