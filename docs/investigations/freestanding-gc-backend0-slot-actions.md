# Investigation: freestanding backend-0 GC slot actions

## Status

resolved

The downstream finalizer gate exposed a separate pre-existing table-lock
reentrancy defect shared by the C oracle and pcc-Python archive.  It is tracked
independently in
`docs/investigations/backend0-finalizer-table-lock-reentrancy.md`; the slot
action ownership change itself is not the cause.

## Problem Description

The shared object-layout/slot-role contract is now owned by
`freestanding_gc_object_slots.py`, but backend 0 still implements subtract,
recursive mark and owned-slot clear callbacks inside managed
`py_obj_gc.py`.  Those actions are raw cycle-collector mechanics and keep the
backend-0 state machine coupled to a managed archive object.

The `gc.get_objects`, `gc.get_referents` and `gc.get_referrers` inspection
APIs are deliberately outside this slice: they construct Python lists and
therefore are not part of the strict raw collector closure.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_backend0_slot_actions.py
```

Expected pre-change result: the strict backend-0 action module is absent.

## Test [CONFIRMED]

Require one strict production owner for subtract/mark/clear callbacks, exact
LLVM/self object closure, no copied object geometry, and an archive-linked
cycle collection proof.  Existing backend-0 weakref/finalizer/root tests stay
as downstream semantic gates.

## Proposals

- No.1 Move backend-0 raw slot actions behind a strict ABI [CONFIRMED]

## No.1 Move backend-0 raw slot actions behind a strict ABI

### Code Change

Create `freestanding_gc_backend0_slots.py` to own the three action families,
recursive reachability and container metadata clearing.  Keep only the
list-producing inspection callback in `py_obj_gc.py`.  Both consume
`pcc_gc_visit_object_slots`; neither duplicates layout rules.

### Resolution

The strict module now uniquely owns all eight backend-0 subtract, mark, and
clear symbols in the production archive.  LLVM, self-backend, and a fresh
no-libpython/self pcc1 compile the real module with an exact four-symbol raw
undefined closure.  Archive-linked collection, referent-layout, finalizer,
weakref, resurrection, and threaded tracking gates are green.  The separately
discovered finalizer lock-reentry defect was fixed and is documented in
`backend0-finalizer-table-lock-reentrancy.md`.

Evidence: `docs/goal/evidence/2026-08-03-freestanding-gc-backend0-slot-actions.md`.
