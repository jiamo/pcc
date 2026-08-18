# GC4 eager enumerate callback roots — 2026-08-24

## Claim

C oracle and strict pcc-Python `py_enumerate_list` now keep the iterator,
returned item and possibly boxed index in updateable Backend-3/4 scheduler-root
slots.  They reload the iterator after every `py_obj_next`, reload item/index
before tuple stores or decrefs, and detach each handle on every error/success
path.  Only the fresh unpublished output and per-element tuple are movement
pinned; both are unpinned before release or return.

This closes eager value-position enumerate.  It does not modify or claim the
callable-iterator state machine inside `py_obj_next`.

## Dynamic proof

C and strict probes enumerate two heap strings starting at index 5, verify the
exact `(5, "x"), (6, "y")` result, prove the returned output is unpinned, and
observe zero scheduler roots after the call.

A strict no-libpython Backend-4 program evaluates value-position
`list(enumerate(Values([7, 8, 9]), 5))`; every custom `__next__` re-enters
`gc.collect()`.  The program completes with
`[(5, 7), (6, 8), (7, 9)]`.  Existing eager and for-loop enumerate neighbors
remain CPython-equal.

## Gates

- static C/strict contract plus C oracle heap-root probe: `2 passed in 0.24s`.
- strict heap-root probe plus Backend-4 callback program:
  `2 passed in 167.34s`.
- existing enumerate semantic neighbors: `3 passed in 7.37s`.
- strict no-libpython source closure: pass.
- C syntax: pass with the pre-existing class-pointer compatibility warning.
- strict archive owner: `1 passed in 1.25s` after the source-hash rebuild.
- runtime ABI chunk plus GC abstraction: `17 passed in 11.71s`.
- task relocation payload/forwarding retirement gate: `24 passed in 15.96s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-py-enumerate-source-c-oracle.log`
- `build/gc4-py-enumerate-callback-roots.log`
- `build/gc4-py-enumerate-semantics.log`
- `build/gc4-py-enumerate-archive-owner.log`
- `build/gc4-py-enumerate-abi-gc.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
0f66183ede421afe8c01b38b85789ade431a356141b12383ab4653c5a44b8f57  pcc/py_runtime/src/py_enumerate.c
bd36d9abb26bc569bd37d83d20c9af02f71ef07b846442e9d2604e700666eff7  pcc/py_runtime/py/py_iter.py
023ffb72faaa8d60e82988e40b951ea09b7b8fa538243fad42d954d90f13a941  tests/python/test_gc_threading_substrate.py
40a7af5c4f4b9cbdb905bddfa7bda25461b92fa0c86cc14c16824bcd180cfa7c  tests/python/test_enumerate_value_builtin.py
4559b15d35d47a9af34bc99342e77d67eca2cae9c60ed0e51e9957fec3161e3f  build/gc4-py-enumerate-source-c-oracle.log
859c0f5fdfa2e16813b86e81b920433ec2a5139ad433ac0521f7efd5e8f2848a  build/gc4-py-enumerate-callback-roots.log
5b05f8d44f368c4e70da4e496f4dd326adb4383991f9c19bc658252ba66526ae  build/gc4-py-enumerate-semantics.log
14e8702365bcc098f7ac06be8ef6ee7085e4afc7eb97413a4e18237d047c98f6  build/gc4-py-enumerate-archive-owner.log
d9b30b701848c1e8d277fd67448a9653835893e9960341dd0edaf82b01a2b3c8  build/gc4-py-enumerate-abi-gc.log
42f1a79a0d2df564125f69a64d97973a2a23b7fec9bfa4cf6445df96b649c191  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.16 eager enumerate callback roots.  The GC4
parent remains `IN_PROGRESS`.
