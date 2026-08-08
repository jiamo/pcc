# Investigation: move default-GC track/untrack into strict freestanding pcc-Python

## Status

resolved

## Problem Description

`py_gc_track` and `py_gc_untrack` maintain the backend-0 raw tracked-object
side table, doubly linked list, object-header flag, and tracked count.  They do
not allocate or traverse managed Python containers, but are still defined by
the managed `py_obj_gc.o` member.  Their only cross-object requirements are the
thread/backend mode queries and the already-freestanding GC index ABI.

The list unlink rule is also used by `py_gc_collect`; copying it into a new
module would create two graph-maintenance implementations.  The migration must
move that helper once and make the managed collector call the same raw helper.

## Repro

`nm -A -g` attributes both public symbols to `py_obj_gc.o`.  Source inspection
shows their bodies depend only on raw memory/global intrinsics,
`pcc_threads_enabled`, `pcc_gc_backend`, `py_gc_index_insert/remove`, and the
shared unlink helper.

## Test

The two red contracts failed as intended. The completed suite now requires
LLVM/self exact object closure, retained-C behavioral parity, unique production
ownership, duplicate track/untrack behavior, GC0..4 execution, and real
pthread contention between collection and four tracking workers.

## Proposals

- No.1 Move track/untrack plus the shared unlink and table-lock helpers [done]
- No.2 Duplicate the unlink algorithm in both modules [DENIED]
- No.3 Remove the threaded backend-4 suppression branch [DENIED]

## No.1 Move track/untrack plus the one shared unlink helper

### Code Change

Add a strict `freestanding_gc_tracking.py` member.  Register only the exact
cross-object ABI signatures it consumes.  Move `_unlink_node` into the strict
member as one internal C-ABI function and make `py_obj_gc.py` call it during
collection.

### Resolution

Accepted. `freestanding_gc_tracking.py` owns `py_gc_track`, `py_gc_untrack`,
the shared unlink helper, and the shared acquire/release lock ABI. The managed
collector calls the same unlink and lock functions.

The migration found and fixed a pre-existing port mismatch: C retried the
stop-the-world gate after safepoints and held the table lock during collection,
whereas pcc-Python returned on the first miss and collected without the lock.
The port now mirrors the C ordering and releases the lock on every post-lock
exit. Evidence is recorded in
`docs/goal/evidence/2026-08-03-freestanding-gc-tracking.md`.

## No.2 Duplicate the unlink algorithm in both modules

### DENIED

Two implementations could drift in prev/next/head/count updates and violate
the one object-graph contract required across all five collectors.

## No.3 Remove the threaded backend-4 suppression branch

### DENIED

That branch is established runtime behavior.  Deleting it would make the split
easier by weakening concurrent/relocating semantics, which is outside the
ownership-only claim.
