# Investigation: freestanding Backend 4 ZPage lifecycle

## Status

resolved

## Problem Description

Backend 4 ZPage allocation and page/node mechanics were strict
freestanding pcc-Python, but empty-page cache/recycle/retire, node/page unlink,
owner lookup/removal, and payload-span cleanup still lived in managed
`py_gc_backend.py`.

The finite boundary excludes one-epoch forwarding retirement and shared
write-barrier/dispatcher policy.

## Repro

The ownership test failed before implementation because
`freestanding_gc_zpage_lifecycle.py` did not exist.  A native differential
then made the intended finite policy observable: eighteen live 2048-byte
objects occupy nine small pages; releasing all objects retains exactly eight
reusable small pages; one allocation consumes one cached page; releasing it
restores the cache to eight; a 70000-byte large page retires without entering
the reusable cache.

## Test [CONFIRMED]

The strict object has exact LLVM and self-emitter closure and defines only the
fifteen intended lifecycle ABIs.  Production archive inspection proves one
owner per symbol.  The full cache-limit/reuse/large-retire sequence matches
the retained C oracle byte for byte.  Adjacent strict mechanics, allocation,
relocation selector/drain, owner-index, retained-span fallback, and source
wiring tests are green.

## Proposals

- No.1 Move the finite ZPage cache/removal lifecycle to one strict object
  [CONFIRMED]

## No.1 Move the finite ZPage cache/removal lifecycle to one strict object

### Code Change

Add `freestanding_gc_zpage_lifecycle.py`; make `py_gc_backend.py` consume its
fifteen exact ABIs; register it in the production archive; add exact lifecycle
and owner-index-remove signatures; and route old source assertions to the new
owner.

### CONFIRMED

Focused tests and the C-oracle differential are green.  A current-source
no-libpython/self pcc1 compiled the real strict module in 0.580 seconds; clang
accepted the IR, the object defines exactly fifteen symbols, and no undefined
symbol is `py_cpy_*`.

## Report

Proposal No.1 landed.  The finite ZPage cache/removal lifecycle is strict
freestanding pcc-Python.  One-epoch forwarding retirement and shared
barrier/dispatcher policy remain explicit open work; the parent task stays
`DONE_WEAK`.
