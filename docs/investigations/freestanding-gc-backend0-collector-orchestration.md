# Investigation: freestanding backend-0 collector orchestration

## Status

resolved

## Problem Description

The backend-0 graph actions, tracked-object table/list, root registries and raw
GC state already have strict freestanding pcc-Python owners.  The public
`py_gc_collect` state machine is still emitted from managed `py_obj_gc.py`,
alongside the list-producing `gc.get_objects`, `gc.get_referents` and
`gc.get_referrers` APIs.  This leaves the raw stop-the-world, reachability,
finalization, weakref clearing and deallocation orchestration inside a managed
archive object even though its underlying graph operations are already strict.

The inspection APIs are deliberately outside this slice because constructing
Python lists is high-level runtime semantics.  This investigation must not
change the backend-0 root set or copy object slot geometry.

Predecessor:
`docs/investigations/freestanding-gc-backend0-slot-actions.md`.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_backend0_collector.py
```

Expected pre-change result: the strict collector source is absent and
`py_gc_collect` is still exported by `py_obj_gc.py`.

## Test [CONFIRMED]

The focused ownership gate fails because
`pcc/py_runtime/py/freestanding_gc_backend0_collector.py` does not exist.
The completed gate will require one strict source/archive owner for
`py_gc_collect`, exact LLVM/self object closure, preservation of the three
managed inspection exports, and structural guards for STW/lock cleanup,
finalizer recheck, weakref invalidation, slot clearing and deallocation order.
Existing backend-0 cycle/finalizer/resurrection/threading tests remain the
semantic oracle.

## Proposals

- No.1 Move the existing collector state machine into a strict object [CONFIRMED]

## No.1 Move the existing collector state machine into a strict object

### Code Change

Create a strict `freestanding_gc_backend0_collector.py` owner for
`py_gc_collect` and its raw private helpers.  Reuse the existing strict
backend-0 subtract/mark/clear ABIs and typed GC globals.  Keep the inspection
APIs in `py_obj_gc.py`; do not alter the collector algorithm or root set.

### CONFIRMED

The strict object now uniquely owns `py_gc_collect` and nine named raw phases.
Both LLVM and self emission have the same exact undefined closure, the current
production archive links one collector owner, and backend-0 cycle,
finalizer/reentrancy/resurrection, weakref, threading and root contracts are
green. The managed module retains only the three list-producing inspection
exports. No root-set or object-layout rule changed.

## Report

No.1 landed as the smallest ownership move. The strict validator was preserved:
private phases became explicit ABI exports and every external call has an exact
name/signature admission. A fresh no-libpython/self pcc1 also compiled the real
collector without `py_cpy_*` calls. Evidence:
`docs/goal/evidence/2026-08-03-freestanding-gc-backend0-collector.md`.
