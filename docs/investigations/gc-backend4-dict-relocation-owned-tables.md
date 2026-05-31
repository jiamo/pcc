# Investigation: Backend #4 dict relocation must retain owned tables

## Status
resolved

## Problem Description
Backend #4 now relocates scalar objects, lists, tuples, sets, and Tasks. A dict
owns two out-of-line tables: `indices` for probing and `entries` for insertion
order. Live entries own both key and value references, while deleted entries
leave tombstones in `indices`. A plain relocation copy would either share the
old tables or fail to make the moved dict own its live keys and values.
Releasing the old forwarded dict could then free tables or key/value objects
still needed by the moved dict.

## Repro
Run the focused C runtime and pcc-Python runtime mirror gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_dict_copy_retains_owned_tables' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_dict_copy_retains_owned_tables' -q -n0
```

Expected result after the fix: Backend #4 can select a dict for relocation,
copy it with distinct `indices` and `entries` tables, `py_incref()` each live
key and value for the moved dict, preserve tombstones and insertion-order
metadata, and keep lookups correct after the root slot follows the forwarding
entry and releases the old dict.

## Test [CONFIRMED]
The focused tests fail before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_dict_copy_retains_owned_tables' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_dict_copy_retains_owned_tables' -q -n0
```

Observed result:

```text
2 failed in 28.90s
stdout: 0
```

Both runtime archives build and run the probe, but Backend #4 does not yet
relocate dicts while preserving moved-dict ownership of `indices`, `entries`,
and live key/value pairs.

## Proposals
- No.1 Add dict-specific relocation table ownership     [CONFIRMED]

## No.1 Add dict-specific relocation table ownership
### Code Change
Add Backend #4-specific relocation support for `PY_TYPE_DICT`. Allocate fresh
`indices` and `entries` tables for the moved dict, copy probe cells and used
entries, `py_incref()` each live key/value pair, then publish moved metadata
only after the copied object is safe for deallocation cleanup.
### CONFIRMED
The focused C runtime and pcc-Python runtime mirror gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_dict_copy_retains_owned_tables' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_dict_copy_retains_owned_tables' -q -n0
```

Observed result:

```text
2 passed in 28.70s
```

The full relocation and abstraction gate passes:

```text
tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py:
34 passed in 209.02s
```

The Backend #3 non-list owned-slot gates still pass:

```text
2 passed in 29.36s
```

## Report (only when the investigation is closing)
No.1 landed. Backend #4 now relocates `PY_TYPE_DICT` as an owner of
out-of-line probe and entry tables: the moved dict receives distinct `indices`
and `entries` buffers, preserves tombstones and insertion-order metadata, and
independently owns every live key/value pair.
