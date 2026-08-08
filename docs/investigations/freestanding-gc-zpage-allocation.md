# Investigation: freestanding Backend 4 ZPage allocation

## Status

resolved

## Problem Description

Backend 4's relocation selection and evacuation drain were strict
freestanding pcc-Python, but the two entry transactions that carve object
storage from a ZPage and then register the owner still lived in managed
`py_gc_backend.py`.

The finite boundary is `pcc_gc_backend4_try_zpage_alloc` plus the owner-tracking
transaction now named `pcc_gc_backend4_zpage_track_alloc`.  Existing active
page, free-page, span reset, node-index, and page-link mechanics are exposed as
eleven exact providers.  Migrating those providers and the removal/cache side
of the lifecycle remains outside this slice.

## Repro

The ownership test failed before implementation because
`freestanding_gc_zpage_allocation.py` did not exist (`1 failed in 0.10s`).
After implementation the strict checker rejected one provider because its
multi-line `extern` declaration did not match the repository's finite
source-signature scanner.  Putting the symbol and exact signature on the same
declaration line admitted only that provider; the scanner was not widened.

## Test [CONFIRMED]

The strict object now owns the two allocation transactions.  C-oracle versus
pcc-Python archive differential probes cover 128-byte small pages, 8192-byte
medium pages, and 70000-byte dedicated large pages.  They compare page counts,
capacity, used bytes, owner offsets, and full release back to the baseline.

## Proposals

- No.1 Move ZPage carving and owner registration to a strict object [CONFIRMED]

## No.1 Move ZPage carving and owner registration to a strict object

### Code Change

Add `freestanding_gc_zpage_allocation.py`; remove both entry definitions from
`py_gc_backend.py`; make managed object tracking consume the new raw ABI; and
name eleven existing provider seams with exact cross-object signatures.

### CONFIRMED

Exact LLVM/self closure, unique production archive ownership, three page-class
differentials, the existing ZPage source/behavior gates, and adjacent strict
relocation suites are green.  A current-source no-libpython/self pcc1 compiled
the real strict module in 0.715 seconds; clang accepted it, the two exports are
definitions, and no undefined symbol is `py_cpy_*`.

## Report

Proposal No.1 landed.  ZPage carving and allocation-to-owner handoff are now
strict freestanding pcc-Python.  The provider mechanics, empty-page cache and
removal lifecycle, and forwarding retirement remain explicit open work; the
parent task stays `DONE_WEAK`.
