# GC4 py_obj_sorted shared-root correction — 2026-08-24

## Claim

`py_obj_sorted` no longer changes the caller's pin lease on its input or
iterator.  C and strict pcc-Python register those caller-visible locals as
updateable scheduler roots on moving backends, reload them after
callback-capable length/iteration calls, and unregister only the root handles
they created.

The output, strict dict-key snapshot and merge scratch are fresh unpublished
working lists, so their constant-count movement pins remain local to the sort.
After every comparison callback, the pre-callback element pointer is discarded
and the selected value is reloaded from the pinned source-list slot before it
is appended.

This supersedes the shared-input/iterator pin part of
`2026-08-24-gc4-py-obj-sorted-callback-pins.md`.  It does not make the global
boolean pin ABI implicitly counted and does not claim other callback families.

## Dynamic proof

The C and strict runtime probes pre-pin the input, call `py_obj_sorted`, and
prove the outer pin remains set.  After the probe explicitly releases that
outer pin, both input and output are again directly eligible for Backend-4
relocation.  Static contracts require updateable input/iterator roots, forbid
their old pin/unpin pairs, and retain balanced pins only for private worklists.

The Backend-3/4 500-item merge and GC4 length-less self-iterator neighbors
remain green.  Strict no-libpython compilation of `py_obj_ops_compare.py`
passes; archive ownership remains uniquely `py_obj_ops_compare.o`.

## Gates

- Backend-3/4 500-item merge: `1 passed in 2.90s`.
- static root contract, C/strict pre-pinned-input balance and GC4 custom
  iterator: `4 passed in 148.99s`.
- strict no-libpython source closure: pass.
- strict archive owner: `1 passed in 139.14s`.
- runtime ABI chunk plus GC abstraction: `17 passed in 10.57s`.
- task relocation payload/forwarding retirement gate:
  `24 passed in 15.03s`.
- C syntax: pass with the pre-existing `PyClassObject *` compatibility warning.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-py-obj-sorted-root-correction-rerun.log`
- `build/gc4-py-obj-sorted-root-merge.log`
- `build/gc4-py-obj-sorted-root-archive-owner.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
96d8a41decc7797866a490e1838d6dacb0f95c5972e67a8a25666a5ae133c58a  pcc/py_runtime/src/py_obj_ops_compare.c
76faee1584545bc373d724e3f22fb6fc7e6a330f42a7f0190acb39e72bd782bb  pcc/py_runtime/py/py_obj_ops_compare.py
1d9e5599e14574f28a110b1fe1fd59ade5ab323531f25608958c35f64908bd84  tests/python/test_gc_threading_substrate.py
c04ea1f489e971de358ff7b3357d118c74a47e5f60b69a35befdb6ef6dbb163a  tests/python/test_native_sorted_custom_iterator.py
203bdc2667866913926bd57bdf7bdbf7c56b4a5b735f2852a58e9ec7ea7cdcdc  build/gc4-py-obj-sorted-root-correction-rerun.log
1480f577532e394c8a58a6337d6cc7e5b53f9e02e927bbe05931a26cbe228c51  build/gc4-py-obj-sorted-root-merge.log
e1a9611d14d4321631f048d7a1d70d419d24c80db429c116cdddc779fb857e7e  build/gc4-py-obj-sorted-root-archive-owner.log
586af655173381b322478874550388a4f5f1e6438e2c12e1f779985c211d6cb4  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.14a.  The GC4 parent remains `IN_PROGRESS` for
the callback families beyond list/sorted and its remaining metadata boundaries.
