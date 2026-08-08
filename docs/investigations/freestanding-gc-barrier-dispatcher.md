# Investigation: freestanding shared GC barrier and dispatcher

## Status

resolved

## Problem Description

All backend-specific collection, relocation, and scheduling slices had strict
freestanding pcc-Python owners, but the public `pcc_gc_step`,
`pcc_gc_note_slot_write_barrier`, and `pcc_gc_note_write_barrier` policy still
lived in the large managed `py_gc_backend.py` object. This was the final known
production GC policy slice without an independent strict object.

The finite boundary is the shared five-backend dispatch and old-to-young /
incremental-concurrent barrier policy. Final cross-backend, root, link-map,
and long-running task gates are separate proof work.

## Repro

Before implementation, the strict-source ownership test failed with
`FileNotFoundError` because `freestanding_gc_barrier_dispatcher.py` did not
exist. After the first implementation, exact closure failed because three
module helpers lacked `@c_abi_export`; strict modules reject hidden Python
helper functions rather than silently inventing internal linkage.

## Test [CONFIRMED]

The initial ownership test was observed red. The final strict object has exact
LLVM and self-emitter closure, production archive inspection proves one owner
per exported symbol, and Backend 3/4 old-to-young barrier results match the
retained C oracle byte for byte. Adjacent incremental/concurrent scheduling,
relocation drain, forwarding retirement, and Backend 3 barrier tests are green.

## Proposals

- No.1 Move shared barrier/dispatch policy into one strict object [CONFIRMED]

## No.1 Move shared barrier/dispatch policy into one strict object

### Code Change

Add `freestanding_gc_barrier_dispatcher.py`; make `py_gc_backend.py` consume
the three public ABIs; expose its existing GC4 store-buffer and step helpers as
explicit cross-object pcc-Python ABIs; register the strict object in the
production archive; and update source ratchets to inspect the actual owner.
The object exports three public ABIs plus three small internal dispatch helpers
required by the strict-module linkage contract.

### CONFIRMED

Focused tests, production archive ownership, and C-oracle differentials are
green. A current-source no-libpython/self pcc1 compiled the strict module in
0.483 seconds; clang accepted the IR, the object defines exactly six symbols,
and no undefined symbol is `py_cpy_*`.

## Report

Proposal No.1 landed. Shared GC step and barrier policy now has a strict
freestanding pcc-Python production owner. This closes the known code-migration
list for `LIBC-P2-FREESTANDING-GC`; the parent remains `DONE_WEAK` until its
root/relocation/synchronization, link-map, one-shot five-GC fixed-point, and
long-running metrics gates are recorded.
