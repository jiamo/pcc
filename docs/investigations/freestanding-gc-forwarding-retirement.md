# Investigation: freestanding Backend 4 forwarding retirement

## Status

resolved

## Problem Description

Backend 4 relocation selection, copy, remap, evacuation drain, and ZPage
lifecycle were strict freestanding pcc-Python, but forwarding removal,
one-epoch source retirement, and delayed source-page destruction still lived
in managed `py_gc_backend.py`.

The finite boundary excludes the shared write-barrier/dispatcher policy.

## Repro

The ownership test failed before implementation because
`freestanding_gc_forwarding_retirement.py` did not exist. A native
differential then made the retirement protocol observable: two forwarded
objects exist after page evacuation; the next GC step heals roots and retires
both entries; later steps keep the forwarding population at zero and report
no old addresses.

## Test [CONFIRMED]

The strict object has exact LLVM and self-emitter closure and defines only the
seven intended forwarding-retirement ABIs. Production archive inspection
proves one owner per symbol. The three-step retirement sequence matches the
retained C oracle byte for byte (`2, 0, 0, 0/verified`). Adjacent relocation,
ZPage, weakref-remap, and retained-source-page tests are green.

## Proposals

- No.1 Move forwarding retirement into one strict object [CONFIRMED]

## No.1 Move forwarding retirement into one strict object

### Code Change

Add `freestanding_gc_forwarding_retirement.py`; make `py_gc_backend.py`
consume its seven exact ABIs; register it in the production archive; add exact
cross-object signatures; and route old source assertions to the new owner.
The strict transaction drains pages parked by the preceding epoch, rewrites
registered roots and live-object slots, marks sources for one epoch, removes
identity/object/forwarding state on the next epoch, and parks a zombie page
only after its forwarding and allocation counts reach zero.

### CONFIRMED

Focused tests and the C-oracle differential are green. A current-source
no-libpython/self pcc1 compiled the real strict module in 0.381 seconds; clang
accepted the IR, the object defines exactly seven symbols, and no undefined
symbol is `py_cpy_*`.

## Report

Proposal No.1 landed. One-epoch forwarding retirement is strict freestanding
pcc-Python. Shared write-barrier/dispatcher policy and the parent task's final
cross-backend/link-map/long-run gates remain open, so
`LIBC-P2-FREESTANDING-GC` stays `DONE_WEAK`.
