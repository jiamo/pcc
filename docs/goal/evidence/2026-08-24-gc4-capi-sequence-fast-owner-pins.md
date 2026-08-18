# GC4 PySequence_Fast raw-storage owner pins — 2026-08-24

## Claim

C production shim, C oracle and strict pcc-Python
`PySequence_Fast_ITEMS` now lifetime-pin the fast-sequence owner before
returning its raw item array:

- tuple returns inline `items` at object offset 24; and
- list returns its out-of-line `items` base.

`PySequence_Fast` already returns an owned reference to an exact list/tuple (or
materializes a tuple for other sequences), but that ownership alone does not
prevent Backend-4 relocation.  The unpaired ITEMS API has no release operation,
so an owner lifetime pin is the explicit movement-stability boundary.
`PySequence_Fast_GET_ITEM` is macro-shaped over ITEMS and inherits the pin.

Direct relocation admission rejects the pinned owners through the PINNED guard
proved in No.13a.

## Dynamic proof

Threaded C and strict probes call `PySequence_Fast` on exact list/tuple,
observe the same owner, obtain exact list out-of-line and tuple inline bases,
read the expected elements through those bases, observe owner PINNED flags and
prove direct relocation add returns 0 for both.

This closes PySequence_Fast raw arrays only.  Unicode/bytes unpaired raw
pointers and counted Py_buffer/memoryview leases remain open, as do constructor
admission blockers, callbacks, resurrection, stage2 performance and fixed point.

## Gates

- C/oracle/strict source-order contract and strict closure: pass.
- final borrowed-item plus sequence raw-view matrix: `6 passed in 1.46s`.
- full source/ABI/GC abstraction neighbors: `32 passed in 32.37s`.
- task relocation payload/forwarding retirement gate:
  `24 passed in 139.96s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-capi-raw-view-pins-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
21a890568f1166c7049fb0a25a272b8246d62c5c6156d92bdb3f04a5154a443e  pcc/py_runtime/src/py_capi_shim.c
92f86e2b2cf254f697a582c294a7c2678b3484e3dc287bbf814d22da0bce3911  pcc/py_runtime/src/py_capi_shim_oracle.c
6d1594357285a1e3809eeab66bbe64c8791601e387fa132cb2d02d66a249b93c  pcc/py_runtime/py/py_capi_sequence_runtime.py
e683f7c465dde8331e30366f47c7375dc88de349d96b1ae34d92a8ddf3170105  pcc/py_runtime/src/py_gc_backend.c
f9304f5251f416fce11d369dcc840812e57fa3643b2e99fff7f61a086c87c5f4  pcc/py_runtime/py/py_gc_backend.py
87efd6fc725371af3686b264a0467f283a7b919ca04ec71e44bf28452c100389  tests/python/test_gc_threading_substrate.py
033ff3c62e3b83235d6b4e3825c6089e836d1f2e3a373515de8214b19270c8a7  build/gc4-capi-raw-view-pins-final.log
a9d5b908d2d1ba91229202083baad4f94cea0cd16db358807bdc507b492a0a48  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.13b PySequence_Fast owner-storage lifetime pins.
The GC4 parent remains `IN_PROGRESS` for string/bytes views and buffers.
