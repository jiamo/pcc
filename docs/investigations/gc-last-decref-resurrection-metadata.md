# Investigation: last-decref resurrection loses GC3/GC4 metadata

## Status

resolved

## Problem Description

Terminal `py_decref` sets `PY_FLAG_GC_DEALLOCATING`, forgets refcount state,
invalidates weakrefs, calls `pcc_gc_note_object_freeing` and `py_gc_untrack`,
then dispatches `py_instance_dealloc`.  A user `__del__` can resurrect the
instance at that point.  The resurrection branch only calls `py_gc_track` and
returns; threaded GC4 makes that track call a no-op, while GC3/GC4 object-index,
node, generation and selector metadata was already removed.  DEALLOCATING also
remains set.

This was discovered and routed from
`gc-backend4-relocation-mutator-quiescence.md`; it is separate from cycle-GC
PEP-442 resurrection and backend-1 phantom-cycle pacing.

## Repro

Add focused C and strict pcc-Python runtime probes for Backend 3 and Backend 4.
A last-drop native `__del__` retains `self` into a registered root.  After the
drop, assert refcount one, DEALLOCATING clear, object-index present, managed
provenance true and the backend generation/selector invariant.  Drop the
resurrected ownership and prove exact-once finalization plus index removal.

## Test [CONFIRMED]

Source inspection confirms both C and strict mirrors use the invalid ordering;
the dedicated dynamic test is being added in this slice.

## Proposals

- No.1 Delay instance metadata removal until finalizer outcome [active]

## No.1 Delay instance metadata removal until finalizer outcome

### Code Change

For instance/user-class terminal decrefs only, keep GC metadata registered
through dealloc dispatch. `py_instance_dealloc` runs `__del__`:

1. if refcount became positive, `py_gc_track` remains an idempotent compatibility
   call, then clear DEALLOCATING only after the still-valid metadata is checked;
2. if refcount remains zero, release fields/dynamic attrs, then perform the
   delayed `pcc_gc_note_object_freeing` + `py_gc_untrack` before ordinary free;
3. preserve Backend-4 zpage's established free-object-memory-before-freeing-note
   ordering so the page is not recycled before the type deallocator finishes.

All other type tags retain the existing generic terminal-decref sequence.

### active

Accepted only with C/strict GC3/GC4 dynamic parity, exact-once later free and
the default resurrection neighbor. A flag-clear-only or metadata reconstruction
from guessed size/origin is forbidden.

## Claim Boundary

This proves last-decref instance resurrection metadata only. It does not change
cycle-GC reachability, weakref policy, finalizer repeat semantics, collector
pacing, Stage2 performance or broad five-GC acceptance.

## Update — 2026-08-25 delayed metadata removal confirmed

### CONFIRMED Proposal No.1

C and strict mirrors delay instance/user-class metadata removal through
`__del__`, validate still-live GC3/GC4 metadata before clearing DEALLOCATING,
and let the instance deallocator own nonresurrection teardown including zpage
ordering. Dedicated C/strict GC3/GC4 probes pass 5/5 and the default neighbor
passes. Exact evidence is in
`docs/goal/evidence/2026-08-25-last-decref-resurrection-metadata.md`.
