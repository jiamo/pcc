# Investigation: freestanding Backend 4 relocation copy transaction

## Status

resolved

## Problem Description

Backend 4's raw payload copier already had a strict freestanding pcc-Python
owner, but the surrounding single-copy transaction still lived in managed
`py_gc_backend.py`.  That transaction decides whether a source is eligible,
allocates and preserves destination residency, installs one forwarding edge,
moves count-on-NEW ownership, consumes the relocation candidate, and hands the
source page back to ZPage policy.

The finite boundary is one public locked copy and its unlocked form for an
already-locked page drain.  Relocation selection, evacuation scheduling,
ZPage data-structure ownership, remap/retirement, and the shared write-barrier
dispatcher remain outside this slice.

## Repro

The ownership test was run before the implementation:

```text
tests/python/test_freestanding_gc_relocation_copy.py::
  test_relocation_copy_has_one_strict_source_owner
1 failed in 0.11s: freestanding_gc_relocation_copy.py did not exist
```

The managed module still defined both `_relocate_copy_unlocked` and the public
`pcc_gc_relocate_copy`, so it was unambiguously the production policy owner.

## Test [CONFIRMED]

The strict test proves exact LLVM/self object closure, exact production archive
ownership, and the full transaction ordering.  Existing pcc-Python probes
prove oversized-copy rejection, single-use candidate consumption, and normal
step relocation.  The prior payload-layout probes continue to exercise the
strict payload object called by this transaction.

## Proposals

- No.1 Move the relocation copy transaction behind two strict ABIs [CONFIRMED]

## No.1 Move the relocation copy transaction behind two strict ABIs

### Code Change

Add `freestanding_gc_relocation_copy.py` as the strict owner of
`pcc_gc_relocate_copy` and `pcc_gc_backend4_relocate_copy_unlocked`.  Export
only finite managed data-structure operations for relocation-set and ZPage
lookup/removal.  Reuse strict forwarding, eligibility, payload, object-size,
allocator, and graph-lock ABIs.

### CONFIRMED

The strict object exports exactly the two copy symbols.  Its undefined set is
limited to eighteen declared raw providers and one telemetry global.  The
production archive has one definition per symbol, both from
`freestanding_gc_relocation_copy.o`.

A fresh self/no-libpython pcc1 was built in 73.288 seconds and compiled the
real strict module in 0.303 seconds.  Clang accepted the emitted IR, both
exports are definitions, and no undefined symbol or call target uses
`py_cpy_*`.

## Report

Proposal No.1 landed.  Backend 4's single-copy transaction is now strict
freestanding pcc-Python while the managed module supplies only finite
data-structure operations still awaiting their own migrations.  Selector,
page evacuation, ZPage retirement, remap retirement, and shared barrier policy
remain explicit open work; the parent task stays `DONE_WEAK`.
