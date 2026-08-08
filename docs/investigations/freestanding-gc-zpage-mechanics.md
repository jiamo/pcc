# Investigation: freestanding Backend 4 ZPage mechanics

## Status

resolved

## Problem Description

Backend 4's strict allocation transaction called eleven page/node provider
ABIs whose implementations still lived in managed `py_gc_backend.py`.  The
boundary covered active-page selection, reusable/free-page lookup, page reset
and span allocation, address-to-page lookup, the bounded node pool, and node
linking into the global/page lists plus owner index.

Empty-page cache/removal and forwarding retirement are separate lifecycle
transactions and remain outside this slice.

## Repro

The ownership test first failed because
`freestanding_gc_zpage_mechanics.py` did not exist.  The first implementation
then failed closed for two useful reasons:

1. strict modules reject private, unexported helper functions, so the small
   class/alignment/layout predicates were inlined rather than exposed as six
   accidental public ABIs;
2. integer division in large-page capacity rounding emitted a managed
   `ZeroDivisionError` path, so power-of-two rounding now uses
   `(size + 65535) & -65536`.

The owner-index upsert call also required an exact cross-object signature.
Only that fixed signature was admitted; the freestanding verifier was not
widened.

## Test [CONFIRMED]

The strict object has exact LLVM and self-emitter closure and defines only the
eleven intended entry symbols.  Production archive inspection proves one
owner per symbol.  A native probe exercises active-page set/find/clear,
reusable-page and address lookup, free-page pop, bounded node-pool reuse, and
global/page-list plus owner-index linking.  Adjacent strict allocation,
relocation selector/drain, and existing Backend 4 source/owner-index tests are
green.

## Proposals

- No.1 Move the eleven ZPage provider mechanics to one strict object
  [CONFIRMED]

## No.1 Move the eleven ZPage provider mechanics to one strict object

### Code Change

Add `freestanding_gc_zpage_mechanics.py`; make `py_gc_backend.py` consume the
eleven exact ABIs; register the strict object in the production archive; add
the missing owner-index upsert signature; and route old source assertions to
the new owner.

### CONFIRMED

Focused tests are green.  A current-source no-libpython/self pcc1 compiled the
real strict module in 0.563 seconds; clang accepted the IR, the object defines
exactly eleven symbols, and no undefined symbol is `py_cpy_*`.

## Report

Proposal No.1 landed.  Page/node provider mechanics are strict freestanding
pcc-Python.  Empty-page cache/removal, one-epoch forwarding retirement, and
shared barrier/dispatcher policy remain explicit open work; the parent task
stays `DONE_WEAK`.
