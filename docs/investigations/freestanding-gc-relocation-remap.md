# Investigation: freestanding Backend 4 relocation remap

## Status

resolved

## Problem Description

`LIBC-P2-FREESTANDING-GC` requires production GC policy to come from strict
freestanding pcc-Python objects, but Backend 4 relocation eligibility and
referent remapping were still defined inside the managed
`py_gc_backend.py` module.  This kept policy coupled to the remaining
relocation copier/selector and made the eventual payload-copy migration harder
to close without duplicating the object graph.

The finite boundary is relocation tag eligibility, one-slot forwarding heal,
and referent remap through the existing `pcc_gc_visit_object_slots` contract.
Forwarding retirement, ZPage destruction, raw payload copying, relocation-set
selection, and write-barrier policy are outside this slice.

## Repro

The new ownership test was run before the implementation:

```text
tests/python/test_freestanding_gc_relocation_remap.py::
  test_relocation_remap_has_one_strict_source_owner
1 failed in 0.10s: freestanding_gc_relocation_remap.py did not exist
```

Existing source tests also still sliced `_remap_referents` and
`_colored_relocate_copy_supported_tag` from `py_gc_backend.py`, proving that
the old managed source remained the asserted owner.

## Test [CONFIRMED]

The strict test proves exact LLVM/self object closure, exact production archive
ownership, the shared object-slot visitor call, forwarding-table healing, and
preservation of the one-epoch `FORWARDED`/`RETIRING` retirement flags in the
managed orchestration.  Existing object-slot, C-extension classification,
container/class metadata, pcc-Python relocation, scheduler-root, payload-copy,
and phase-reset probes were routed to the new owner and remain green.

## Proposals

- No.1 Move eligibility and remap behind four strict ABI exports [CONFIRMED]

## No.1 Move eligibility and remap behind four strict ABI exports

### Code Change

Add `freestanding_gc_relocation_remap.py` with strict owners for:

- `pcc_gc_backend4_relocate_copy_supported_tag`;
- `pcc_gc_backend4_remap_heal_slot`;
- `pcc_gc_backend4_remap_slot`;
- `pcc_gc_backend4_remap_referents`.

Keep referent enumeration in the already-strict
`pcc_gc_visit_object_slots` ABI.  Make `py_gc_backend.py` consume the new
eligibility/heal/remap exports while retaining the still-open relocation-copy,
selection, ZPage, and one-epoch retirement orchestration.

### CONFIRMED

The strict module exports exactly the four symbols and imports only the
C-extension tag classifier, forwarding lookup, scalar oldification tag
classifier, and shared object-slot visitor.  The production archive has one
definition per symbol, all from `freestanding_gc_relocation_remap.o`.

A fresh self/no-libpython pcc1 was built in 33.962 seconds and compiled the real
strict module.  Clang accepted the emitted IR, all four exports are definitions,
and no call/invoke target uses `py_cpy_*`.

## Report

Proposal No.1 landed.  Backend 4 relocation eligibility and shared-slot remap
now have a strict freestanding owner without creating a second per-type graph
rule.  Raw payload copying, relocation copy/selection, ZPage handoff, and
one-epoch forwarding retirement remain explicit open work; the parent task
therefore remains `DONE_WEAK`.
