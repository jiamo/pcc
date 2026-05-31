# Investigation: Backend #4 set relocation must retain owned entries

## Status
resolved

## Problem Description
Backend #4 now relocates scalar objects, lists, tuples, and Tasks. A set owns an
out-of-line `entries` table whose live keys are owned references and whose
tombstones must remain as `py_set_dummy`. A plain relocation copy would either
share the old `entries` table or fail to make the moved set own its live keys.
Releasing the old forwarded set could then free the moved set's entries or live
keys.

## Repro
Run the focused C runtime and pcc-Python runtime mirror gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_set_copy_retains_owned_entries' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_set_copy_retains_owned_entries' -q -n0
```

Expected result after the fix: Backend #4 can select a set for relocation,
copy it with a distinct `entries` table, `py_incref()` each live key for the
moved set, preserve tombstones and table metadata, and keep membership checks
correct after the root slot follows the forwarding entry and releases the old
set.

## Test [CONFIRMED]
The focused tests fail before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_set_copy_retains_owned_entries' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_set_copy_retains_owned_entries' -q -n0
```

Observed result:

```text
2 failed in 30.06s
stdout: 0
```

Both runtime archives build and run the probe, but Backend #4 does not yet
relocate sets while preserving moved-set ownership of the entries table and
live keys.

## Proposals
- No.1 Add set-specific relocation entry ownership     [CONFIRMED]

## No.1 Add set-specific relocation entry ownership
### Code Change
Add Backend #4-specific relocation support for `PY_TYPE_SET`. Allocate a fresh
`SetEntry` table for the moved set, copy hashes and tombstone/empty markers,
`py_incref()` each live key, then publish the moved table metadata only after
the copy is complete enough for deallocation cleanup.
### CONFIRMED
The focused C runtime and pcc-Python runtime mirror gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_set_copy_retains_owned_entries' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_set_copy_retains_owned_entries' -q -n0
```

Observed result:

```text
2 passed in 29.11s
```

The full relocation and abstraction gate passes:

```text
tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py:
32 passed in 180.73s
```

The Backend #3 non-list owned-slot gates still pass, confirming Backend #3
oldification support was not widened:

```text
2 passed in 29.07s
```

## Report (only when the investigation is closing)
No.1 landed. Backend #4 now relocates `PY_TYPE_SET` as an owner of an
out-of-line entries table: the moved set receives a distinct table, preserves
empty and tombstone slots, and independently owns each live key. Root-slot
read-barrier healing can release the old forwarded set while leaving moved-set
membership checks correct.
