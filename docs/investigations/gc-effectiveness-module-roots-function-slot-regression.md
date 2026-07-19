# GC effectiveness: module roots and stale pcc-Python function slots

## Status

resolved (2026-07-18)

## Problem Description

Four current-source backend-0 effectiveness checks failed together: a
self-list and closure-cell cycle survived collection, while two tuple-unpack
checks reported three more tracked objects than their historical absolute
counts.  This is a successor to
`gc-effectiveness-closure-cell-cycle.md`; that earlier fix remained present,
so the current failure needed a fresh object-graph audit.

## Deterministic reproduction

The four reported nodes failed together in 1.84 seconds.  The count mismatches
were exact and stable: `5` versus `2`, and `6` versus `3`.

LLDB inspection at `py_gc_get_count` classified the additional three objects
as reachable module state, not leaked iteration state: two module-level
`PY_TYPE_FUNC` objects and the module dictionary.  Consequently the semantic
contract is an exact delta from the startup baseline, not an absolute count
that assumes a program has no tracked module roots.

For the closure failure, the post-collection unreachable component was:

```text
function -> wrapper tuple -> captures tuple -> closure-cell list
         -> payload list -> function
```

After subtracting internal references, the wrapper tuple retained one
`gc_refs` edge.  The C layout and the pcc-Python backend walker own six
function slots at offsets `24, 32, 40, 64, 80, 88`, but
`py_obj_gc.py::_py_obj_gc_visit_fixed_owner_slots` still visited only the old
`24, 40` layout.  In particular it omitted the captures slot at offset 64.

## Fix

- Make backend-0's pcc-Python function walker consume all six current owned
  slots.
- Add a source-contract regression requiring the backend-0 and general GC
  walkers to agree on those offsets.
- Keep the effectiveness assertions exact, but express tracked-object
  expectations as deltas from the measured module-root baseline.
- Repair an adjacent source-contract test to delimit a C helper by the stable
  `pcc_capi_module_from_def` function boundary rather than obsolete comment
  wording.

No root, finalizer, weakref, or collection behavior was disabled.  The count
change does not hide growth: it requires the same exact `+2`/`+3` deltas and
requires the self-cycle to return exactly to its starting baseline.

## Confirmation

```text
four reported nodes + function-slot source contract: 5 passed in 1.97s
tests/python/test_gc_effectiveness.py: 27 passed in 15.88s
tests/python/test_gc_update_referents.py: 31 passed in 35.64s
```

## Report

The real semantic regression was stale pcc-Python backend-0 function-slot
metadata, which made closure captures invisible to cycle subtraction.  The
three-object count shift was a separate test-assumption bug caused by valid
module roots.  Both boundaries now have exact regressions.

