# Investigation: freestanding Backend 4 relocation drain

## Status

resolved

## Problem Description

Backend 4 relocation eligibility, payload copying, copy transactions, and
page-grouped selection already had strict freestanding pcc-Python owners, but
the bounded object drain and whole-page drain still lived in managed
`py_gc_backend.py`.  Those drains are the transaction boundary that consumes
the selected set, preserves page handoff until the set is empty, accounts
incomplete batches, and invokes one-epoch remapping after evacuation.

The finite boundary is the two public drain ABIs and six named internal drain
stages.  ZPage allocation/reuse/destruction and forwarding retirement remain
outside this slice; the latter is consumed through an explicitly named
`pcc_gc_backend4_remap_and_retire_unlocked` provider.

## Repro

The ownership test failed before implementation because
`freestanding_gc_relocation_drain.py` did not exist (`1 failed in 0.10s`).
The first strict compile then correctly rejected unnamed top-level helpers.
After naming them, the fail-closed cross-object checker rejected the previously
undeclared remap and public copy seams.  Adding their exact signatures—not a
prefix wildcard—made the finite closure explicit.

## Test [CONFIRMED]

The strict object owns both public drains and six helper ABIs.  Its safepoint
cadence uses `(moved & 15) == 0`, avoiding implicit division-exception
machinery.  C-oracle differential probes prove object-budget, page-budget, and
the real managed `pcc_gc_step(1)` dispatcher paths have identical moved counts,
page handoff, incomplete-batch accounting, relocation-set exhaustion, and
evacuation efficiency.

## Proposals

- No.1 Move the bounded evacuation drains to a strict object [CONFIRMED]

## No.1 Move the bounded evacuation drains to a strict object

### Code Change

Add `freestanding_gc_relocation_drain.py`; move the two public drain
definitions and their six finite stages out of `py_gc_backend.py`; make the
managed GC step consume the strict public ABI.  Export the existing managed
remap transaction under an unlocked provider name so its later migration has
an explicit, testable boundary.

### CONFIRMED

Exact LLVM/self closure, unique production archive ownership, five adjacent
relocation suites, the existing page-handoff probes, and three C-oracle versus
pcc-Python archive differential modes are green.  A fresh no-libpython/self
pcc1 compiled the strict module in 0.326 seconds; clang accepted the output,
all eight exports are definitions, and no undefined symbol is `py_cpy_*`.

The fresh stage1 took 183.220 seconds because this source identity had zero
self-object cache hits and 325 misses.  Its profile attributes 114.942 seconds
to native object emission and 40.904 seconds to runtime construction.  The
preceding selector stage had 321 hits, four misses, and a 70.179-second total,
so the elapsed difference is classified as cache population rather than a
drain-path regression.

## Report

Proposal No.1 landed.  Backend 4 bounded evacuation drain policy is now strict
freestanding pcc-Python.  ZPage lifecycle, one-epoch forwarding retirement,
and the shared barrier/dispatcher remain explicit open work; the parent task
stays `DONE_WEAK`.
