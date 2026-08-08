# Investigation: freestanding Backend 4 relocation payload copying

## Status

resolved

## Problem Description

`LIBC-P2-FREESTANDING-GC` requires production GC policy to come from strict
freestanding pcc-Python objects.  Backend 4 relocation eligibility and referent
remapping already had a strict owner, but raw payload copying still lived in
the managed `py_gc_backend.py` module.  That left relocation correctness for
continuations, traceback storage, classes, weakrefs, tasks, instances, and
container backing arrays coupled to the remaining GC4 selector/ZPage policy.

The finite boundary for this slice is the shared slot-pair walk plus raw
per-type payload copying.  Relocation-set selection, copying-object allocation,
ZPage evacuation/retirement, one-epoch forwarding retirement, and the shared
write-barrier dispatcher remain outside this slice.

## Repro

The ownership test was written before the implementation:

```text
tests/python/test_freestanding_gc_relocation_payload.py::
  test_relocation_payload_has_one_strict_source_owner
1 failed in 0.10s: freestanding_gc_relocation_payload.py did not exist
```

Existing source tests also sliced `_relocate_copy_payload`,
`_relocate_copy_slots`, and three relocation slot callbacks from
`py_gc_backend.py`, proving that the old managed module remained the asserted
owner.

## Test [CONFIRMED]

The strict ownership test proves exact LLVM/self object closure, exact
production archive ownership, a finite raw ABI import set, and absence of
managed definitions.  Source-contract tests prove that the copier pairs source
and destination slots through `pcc_gc_visit_object_slots`, heals forwarded
source slots, preserves owned-reference accounting, retargets registered
continuation roots and remembered slots, and registers copied payload spans.

Real pcc-Python probes cover task/scheduler forwarding and list, tuple, task,
set, dict, and instance payload layouts.  A fresh self/no-libpython pcc1 also
compiles the real strict source without `py_cpy_*` calls.

## Proposals

- No.1 Move shared payload copying behind strict ABI exports [CONFIRMED]

## No.1 Move shared payload copying behind strict ABI exports

### Code Change

Add `freestanding_gc_relocation_payload.py` as the strict owner of
`pcc_gc_relocate_copy_payload` and nine finite callback/lifecycle helpers.
Reuse the already-strict object-slot and remap ABIs rather than creating a
second object-graph walk.  Make `py_gc_backend.py` consume the payload-copy ABI
while retaining the still-open allocation, selection, ZPage handoff, and
retirement orchestration.

### CONFIRMED

The strict object has exactly ten public definitions.  Its undefined set is
limited to the declared raw allocation/memory providers, object-slot visitor,
remap/remembered/ZPage hooks, continuation/weakref roots, and refcount
primitive.  The production archive has one owner for
`pcc_gc_relocate_copy_payload`, from
`freestanding_gc_relocation_payload.o`.

A fresh self/no-libpython pcc1 was built in 76.172 seconds and compiled the
real strict module in 1.079 seconds.  Clang accepted the emitted IR, all ten
exports are definitions, and no undefined symbol or call target uses
`py_cpy_*`.

## Report

Proposal No.1 landed.  Backend 4 raw payload copying now has a strict
freestanding pcc-Python owner and continues to consume the single shared slot
contract.  Copy/allocation selection, ZPage evacuation/retirement, one-epoch
forwarding retirement, and shared write-barrier/dispatcher policy remain
explicit open work, so the parent task remains `DONE_WEAK`.
