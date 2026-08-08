# Investigation: move raw GC control exports into strict freestanding pcc-Python

## Status

resolved

## Problem Description

`LIBC-P2-FREESTANDING-GC` requires every production collector symbol family to
belong to a strict freestanding pcc-Python closure.  Eleven public functions in
`py_obj_gc.py` only inspect or mutate raw GC state/object-header bits, but their
definitions currently live in the managed `py_obj_gc.o` member:

```text
py_gc_init
py_gc_enable / py_gc_disable / py_gc_is_enabled
py_gc_is_tracked / py_gc_get_count
py_gc_get_threshold / py_gc_set_threshold
py_gc_freeze / py_gc_unfreeze / py_gc_get_freeze_count
```

None performs collection, graph traversal, finalization, weakref processing,
allocation, or managed-container construction.  Keeping them in the managed
collector object unnecessarily widens the dependency root for the public GC
control surface.

## Repro

On the current production archive, `nm -A -g` attributes all eleven definitions
to `py_obj_gc.o`.  The source confirms that each body uses only raw
`global_addr`/load/store operations, tagged/null checks, and the object header.

## Test [CONFIRMED]

The first source/archive ownership regression failed because
`freestanding_gc_control.py` did not exist.  After the move, the focused suite
compiles the module with LLVM and self emitters, requires exactly eleven public
definitions and six raw state imports, proves unique production archive
ownership, and compares a deterministic control sequence with the retained C
runtime under GC0..4.

## Proposals

- No.1 Extract the eleven raw control exports unchanged [CONFIRMED]
- No.2 Mark all of `py_obj_gc.py` freestanding immediately [DENIED]
- No.3 Duplicate the exports in both modules and let archive order select one [DENIED]

## No.1 Extract the eleven raw control exports unchanged

### Code Change

Create `freestanding_gc_control.py`, move the eleven bodies without semantic
changes, archive it through `FREESTANDING_PY_MODULES`, and delete those exports
from `py_obj_gc.py`.  The strict module imports only compiler-recognized raw
memory/global intrinsics.

### CONFIRMED

The initial test produced the intended red result (`FileNotFoundError` for the
missing strict owner).  The completed focused suite reports:

```text
4 passed in 57.42s
```

Both generated objects have exactly the eleven public exports and only these
six undefined raw state symbols: enabled, tracked count, three thresholds, and
freeze count.  The content-addressed production archive uniquely attributes
all eleven definitions to `freestanding_gc_control.o`; `py_obj_gc.o` defines
none.  A single C harness exercises initialization, enable/disable, threshold
updates including negative preservation, count/freeze behavior, and
null/tagged/clear/set tracked flags.  Its pcc-Python output matches the retained
C runtime byte-for-byte under all five backend environment selections.

A fresh self/no-libpython pcc1 from the immediately preceding telemetry slice
compiled the new strict module successfully.  Its object repeats the exact
eleven-definition/six-import contract.  Existing public ABI, backend selector,
native gc module, C-extension source guard, and backend compile tests also pass.

## No.2 Mark all of `py_obj_gc.py` freestanding immediately

### DENIED

The remaining object contains cycle collection, managed list construction,
weakref/finalizer/deallocation callbacks, C-extension slot traversal, and
stop-the-world coordination.  Relabeling it without splitting and proving that
closure would either fail strict verification or weaken the boundary.

## No.3 Duplicate the exports in both modules and let archive order select one

### DENIED

Duplicate definitions make ownership link-order-dependent and can silently
select the managed copy.  The migration requires one public definition per
symbol and an exact archive link-map proof.

## Report

### Resolution

The state-only and object-header-only GC control ABI is now a strict
freestanding pcc-Python member.  The move removed 82 lines from the managed
`py_obj_gc.py` owner without changing public names or behavior.

### Claim boundary

This does not move `py_gc_collect`, track/untrack, referent/referrer list
construction, finalizers, weakrefs, C-extension slot traversal, or any
backend-1..4 graph algorithm.  Those remain part of the active task boundary
and require their own semantic and production ownership proof.
