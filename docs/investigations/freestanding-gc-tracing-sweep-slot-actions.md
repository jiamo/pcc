# Investigation: freestanding tracing sweep slot actions

## Status

resolved

## Problem Description

The common mark cycle for tracing GC backends 1 through 4 now has a strict
freestanding pcc-Python owner, but PASS-1 clearing remains in managed
`py_gc_backend.py`.  Backend 0 also owns a second copy of the same
list/tuple/dict/set metadata-clearing logic.  The candidate policy differs
(backend-0 reachability table versus tracing sweep-candidate flag), while the
owned-slot clearing and container metadata reset contract should have one
implementation family.

This slice moves only candidate-aware owned-slot clearing, shared metadata
reset and tracing weakref invalidation.  PASS-0 finalizer dispatch, resurrection
recheck, PASS-2 deallocation and backend scheduling remain outside the slice.

Predecessor:
`docs/investigations/freestanding-gc-common-mark-cycle-orchestration.md`.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_tracing_sweep_slots.py
```

Expected pre-change result: the strict shared clear source is absent and both
`py_gc_backend.py` and `freestanding_gc_backend0_slots.py` still own separate
clear-referents implementations.

## Test [CONFIRMED]

The pre-change focused gate failed because `freestanding_gc_sweep_slots.py`
did not exist.  The completed gate requires one strict source/archive owner,
exact LLVM/self closure, distinct backend-0/tracing candidate guards, one
metadata reset phase, weakref-before-clear ordering and preservation of the
existing backend-0 and five-backend lifetime/weakref/finalizer contracts.

## Proposals

- No.1 Share strict clear-referents mechanics with distinct candidate guards [CONFIRMED]

## No.1 Share strict clear-referents mechanics with distinct candidate guards

### Code Change

Create `freestanding_gc_sweep_slots.py` with backend-0 and tracing clear-slot
callbacks plus one shared container metadata reset.  Move the existing
backend-0 clear exports into that object, export tracing clear-referents and
clear-unreachable phases, and leave mark/subtract actions in
`freestanding_gc_backend0_slots.py`.  Do not change flag values or clear order.

### CONFIRMED

The strict source owns all seven candidate/slot/metadata/PASS-1 exports and is
the unique production-archive owner.  LLVM and self objects expose the same
five-symbol raw closure.  Backend 0 still queries its reachability table;
tracing backends still query the sweep-candidate bit.  Both paths clear the
owned slot before decref, while tracing invalidates weakrefs before clearing
referents.  Focused backend-0, slot-contract and five-backend lifetime,
weakref, finalizer and resurrection gates remain green, and a fresh
no-libpython/self pcc1 compiles the strict source without `py_cpy_*` calls.

## Report

No.1 landed without moving PASS-0 finalizer dispatch, resurrection recheck,
PASS-2 deallocation or backend scheduling.  Evidence:
`docs/goal/evidence/2026-08-03-freestanding-gc-sweep-slots.md`.
