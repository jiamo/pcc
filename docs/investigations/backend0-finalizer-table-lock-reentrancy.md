# Investigation: backend-0 finalizer re-enters the tracked-object table lock

## Status

resolved

## Problem Description

Backend 0 runs user `__del__` methods while `py_gc_collect()` holds the
tracked-object table lock.  A finalizer that creates a tracked temporary (the
ordinary `runs.append(...)` bound-method path is sufficient) re-enters
`py_gc_track()` on the collector thread and spins forever on that same
non-reentrant lock.

This is a pre-existing parity bug in both the retained C oracle and the
pcc-Python production archive.  It is distinct from reentrant `gc.collect()`:
that operation is already guarded, while this failure enters track/untrack as
a side effect of otherwise ordinary finalizer execution.

## Repro

`tests/python/test_freestanding_gc_backend0_slot_actions.py` compiles one
self-cycle with `__del__`, links it separately against the retained C runtime
and the pcc-Python runtime archive, and gives each executable a five-second
deadline.  Before the fix, both executables time out.

Runtime logging stops immediately after finalizer dispatch creates/increments
the bound method.  `/usr/bin/sample` attributes the blocked main thread to
`pcc_gc_default_table_lock` / `pcc_thread_safepoint` for the entire sample.

## Test [CONFIRMED]

The regression must finish under the five-second child-process deadline for
both runtime archives, run the finalizer exactly once, and reclaim the
self-cycle.  Existing resurrection, weakref, finalizer, and threaded tracking
tests remain downstream semantic gates.

## Proposals

- No.1 Native-owner lock reentry plus deferred node reclamation [CONFIRMED]
- No.2 Release the table lock around arbitrary user code [DENIED]
- No.3 Skip tracking while finalizers run [DENIED]

## No.1 Native-owner lock reentry plus deferred node reclamation

### Code Change

The table lock publishes the acquiring native thread's identity token in an
atomic i64 slot.  The token is the address of a C11 `_Thread_local` byte, so it
remains unique across live raw pthreads even in a runtime built with
`PCC_WITH_THREADS=0`.  Only the exact owner may mutate the table reentrantly;
other native threads still acquire the byte lock normally.

An untracked node must not be freed immediately: candidate arrays retain
`PyGcNode *` values across finalizer calls.  Instead unlink it, remove its
index entry, clear its object pointer, queue the node on a raw deferred-free
list, and drain that list before clearing `py_gc_collecting` and releasing the
lock.

The C oracle and strict pcc-Python tracking member must implement the same
transition.  Candidate loops must treat a deferred node's null object pointer
as already reclaimed.

### Rejected intermediate hypothesis

The first implementation treated global `py_gc_collecting != 0` as proof that
the current thread owned the lock.  The raw-pthread contention gate aborted:
foreign pthreads observed the same global flag and bypassed mutual exclusion.
That result disproved the owner inference and led to the native TLS token
protocol above.

### Resolution

Both the retained C oracle and production pcc-Python archive now use the same
owner-token and deferred-node protocol.  A five-second finalizer regression
proves each archive runs `__del__` exactly once and returns the tracked count
to its pre-cycle baseline.  Four raw pthreads prove their tokens are non-null
and pairwise distinct, while the contention harness preserves all 1,024
track/untrack transitions.  Directed weakref, resurrection, reentrant collect,
and backend-0 production contracts are green.

Evidence: `docs/goal/evidence/2026-08-03-freestanding-gc-backend0-slot-actions.md`.

## No.2 Release the table lock around arbitrary user code

### DENIED

The current candidate array stores raw side-table node pointers.  Releasing
the lock without pinning or detaching candidates lets finalizer-side untrack
free those nodes, producing a use-after-free when collection resumes.

## No.3 Skip tracking while finalizers run

### DENIED

Dropping track/untrack events weakens object-graph semantics and can hide live
cycles created by finalizers.  The table mutation must occur, with node
reclamation deferred only as long as the collector owns candidate pointers.
