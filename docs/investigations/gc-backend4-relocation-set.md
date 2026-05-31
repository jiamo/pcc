# Investigation: Backend #4 needs a relocation set

## Status
resolved

## Problem Description
Backend #4 is meant to model a ZGC-style colored relocating collector.
`goal.md` lists `relocation set` as a missing mechanism: after marking, the
collector needs a separate set of objects/pages selected for evacuation.  The
current backend can follow explicit forwarding entries and has stable object
IDs, but `pcc_gc_step()` still only toggles an object flag; there is no
relocation-set side table or ABI that later movement work can consume.

## Repro
Run the focused backend #4 relocation-set gate:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_relocating.py::test_colored_relocating_selects_unpinned_relocation_set -q -n0
```

Expected before the fix: the probe fails to link because relocation-set ABI
symbols are missing from the runtime.

## Test [CONFIRMED]
`tests/test_gc_backend_relocating.py::test_colored_relocating_selects_unpinned_relocation_set`

Observed before the fix on 2026-05-07:

```text
ld: Undefined symbols:
  _pcc_gc_relocation_set_contains
  _pcc_gc_relocation_set_size
  _pcc_gc_reset_relocation_set
  _pcc_gc_select_relocation_set
pcc.py_frontend.pipeline.PyPipelineError: clang link failed (exit 1)
```

## Proposals
- No.1 Add object-level backend #4 relocation set     [CONFIRMED]

## No.1 Add object-level backend #4 relocation set
### Code Change
Add an object-level relocation-set side table for backend #4, with public
runtime helpers to reset the set, select unpinned known objects up to a budget,
query membership, and inspect current set size.  Reuse this helper from the
backend #4 `pcc_gc_step()` path and mirror the implementation in the
pcc-Python runtime port.
### CONFIRMED
Implemented the object-level relocation-set side table in
`pcc/py_runtime/src/py_gc_backend.c` and mirrored it in
`pcc/py_runtime/py/py_gc_backend.py`.  The runtime now exports:

- `pcc_gc_reset_relocation_set()`
- `pcc_gc_select_relocation_set(budget)`
- `pcc_gc_relocation_set_contains(obj)`
- `pcc_gc_relocation_set_size()`

`pcc_gc_step()` for backend #4 now selects relocation-set entries through this
helper instead of directly toggling the candidate bit.  The public ABI table in
`pcc/py_frontend/codegen/runtime_abi.py` and the abstraction-surface test were
updated with the new relocation-set helpers and the prior `pcc_gc_object_id`
helper.

Observed verification:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_relocating.py::test_colored_relocating_selects_unpinned_relocation_set -q -n0
# 1 passed in 24.98s

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py -q -n0
# 18 passed in 7.75s

/opt/homebrew/bin/timeout 180s env -u LC_ALL make -B -C pcc/py_runtime libpy_runtime.a
# passed; existing unused-function warning in py_gc_backend.c remains

/opt/homebrew/bin/timeout 420s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
# passed

/opt/homebrew/bin/timeout 420s env -u LC_ALL uv run pytest tests/test_gc_threading_substrate.py tests/test_gc_backend_*.py tests/test_gc_effectiveness.py -q -n0
# 48 passed, 3 xfailed, 3 xpassed in 42.58s

/opt/homebrew/bin/timeout 420s env -u LC_ALL PCC_GC_BACKEND=4 uv run pytest tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q -n0 -rxX
# 9 passed, 1 xfailed, 5 xpassed in 8.82s
```

`nm` also confirms that both runtime archives export all four relocation-set
symbols.  Black was attempted but unavailable in the active environment:
`pyenv: black: command not found`.

## Report (only when the investigation is closing)
No.1 landed.  Backend #4 now has a concrete relocation-set selection surface:
known unpinned objects can be selected up to a budget, membership is tracked in
a side table, duplicate selection is avoided, freeing an object removes it from
the set, and reset clears both side-table membership and the candidate bit.

This is still narrower than ZGC.  It is object-level, not page-based; it does
not yet copy objects to new storage, choose pages by fragmentation, or drive a
full relocate phase.  `tasksV2.md` backend #4 therefore remains
`research partial`.
