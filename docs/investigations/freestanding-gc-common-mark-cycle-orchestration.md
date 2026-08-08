# Investigation: freestanding common tracing mark-cycle orchestration

## Status

resolved

## Problem Description

The strict freestanding GC objects already own the object-slot geometry,
known-object/root-gray operations, object-list mark preparation, registered
root scanners and refcount external-root scan.  The shared tracing state
machine used by backends 1 through 4 still lives inside the managed
`py_gc_backend.py` object: trace-slot dispatch, transitive gray draining, mark
cycle begin and the stop-the-world termination cut that creates sweep
candidates.

Moving only the four orchestration helpers would leave the strict object
calling a managed `_trace_referents` implementation and would not close an
actual collector boundary.  This slice therefore includes the common TRACE
slot action while continuing to consume the single existing
`pcc_gc_visit_object_slots` contract.  Sweep/finalizer/deallocation and each
backend's scheduling policy remain outside this finite slice.

Predecessor:
`docs/investigations/freestanding-gc-backend0-collector-orchestration.md`.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_common_mark_cycle.py
```

Expected pre-change result: the strict common mark-cycle source is absent and
the managed backend still defines `_trace_referents`, `_seed_roots`,
`_drain_all_gray_unlocked`, `_begin_mark_cycle` and
`_finish_tracing_cycle`.

## Test [CONFIRMED]

The pre-change focused gate failed because
`freestanding_gc_common_mark_cycle.py` did not exist.  The completed gate will
require a single strict source/archive owner for the
six raw phases, an exact LLVM/self undefined closure, structural ordering for
root seeding and STW termination, and managed callers that use only explicit
raw ABI declarations.  It will be marked CONFIRMED after the pre-change
failure is observed.

## Proposals

- No.1 Move the shared mark-cycle closure into one strict object [CONFIRMED]

## No.1 Move the shared mark-cycle closure into one strict object

### Code Change

Create `freestanding_gc_common_mark_cycle.py` with exported raw trace-slot,
trace-referents, seed, gray-drain, begin-cycle and finish-cycle phases.  Reuse
the strict object-slot and root operations through exact extern signatures;
replace the managed definitions with extern aliases.  Do not change mark
flags, root order, safepoints, STW order or sweep behavior.

### CONFIRMED

The final strict object owns seven raw phases, including the separate
black-preserving child-gray transition found necessary by the red resurrection
gate.  LLVM/self exact-closure tests, production archive ownership, focused
source contracts, five-backend lifetime/root/slot/weakref/finalizer semantics
and a fresh no-libpython/self pcc1 are green.  Managed callers retain only
extern aliases for the five orchestration entrypoints.

## Update: referent tracing cannot reuse root re-graying

The first implementation reused `pcc_gc_mark_root_gray_if_known` inside the
TRACE slot callback.  The focused five-backend resurrection gate then hung on
backend 1, and a macOS sample showed all CPU time cycling through
`pcc_gc_drain_all_gray_unlocked` -> `pcc_gc_trace_slot` -> root gray.

The two operations intentionally differ.  Root graying at the mark-termination
cut may re-gray a black object so mutations since the initial scan are traced.
Normal referent tracing must preserve black objects; the original managed
`_mark_gray_if_known` guarded its gray transition with `(flags & 32) == 0`.
The strict module must therefore own a separate
`pcc_gc_trace_mark_gray_if_known` phase with that black guard.  A structural
test now prohibits calling the root-gray primitive from the TRACE slot.

## Report

No.1 landed with the required trace-gray/root-gray separation.  The slice
moves the complete common mark closure without moving sweep or backend policy
and continues to consume the one strict object-slot contract.  Evidence:
`docs/goal/evidence/2026-08-03-freestanding-gc-common-mark-cycle.md`.
