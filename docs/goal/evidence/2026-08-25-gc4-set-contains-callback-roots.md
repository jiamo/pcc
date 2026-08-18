# GC4 set contains callback roots — 2026-08-25

## Claim

C and strict pcc-Python `py_set_contains` now root set/item before user hash
and use a restartable probe.  Equality candidates are retained and rooted;
after callback return, owner/item/candidate are reloaded and the probe restarts
if owner identity, entries, capacity or the current slot/key changed.

This slice is read-only membership.  Set add/remove/update remain separate.

## Dynamic proof

C and strict probes cover both supported callback surfaces: an ordinary
instance `__hash__` directly relocates one set, and a C-extension equality
callback directly relocates a second.  Membership remains true before and
after reload, each relocation occurs once, and only the two external roots
remain before cleanup.

## Gates

- static C/strict seam plus C callback probe: `2 passed in 0.21s`.
- strict hash/equality callback probe: `1 passed in 142.54s`.
- set membership semantics: `1 passed in 2.00s`.
- strict no-libpython source closure and C syntax: pass.
- strict archive owner: `1 passed in 141.15s`.
- runtime ABI chunk plus GC abstraction: `17 passed in 35.34s`.
- task relocation payload/forwarding retirement gate: `24 passed in 15.22s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-set-contains-source-c-oracle.log`
- `build/gc4-set-contains-callback-roots.log`
- `build/gc4-set-contains-semantics.log`
- `build/gc4-set-contains-archive-owner.log`
- `build/gc4-set-contains-abi-gc.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
3a487d5ffa380f1884853a56d8fd600364b315bbd9c82b81c99c3dba2e255852  pcc/py_runtime/src/py_set.c
bd2c32b8fb60cf528e89d8a81375afa1c978297ccba824e5b26cb78838e1a435  pcc/py_runtime/py/py_set.py
affa75867cc578edf7676316b0d9a3d310aa12570e3823393ae8cd26f2bdf6d1  tests/python/test_gc_threading_substrate.py
99dbaec4ad4061adefa0cd52898cc2ec7bec15d5a00c57090aa014eb995999bf  build/gc4-set-contains-source-c-oracle.log
ffa7da82a33817d6807ed0805951cb630d7a6cefbb9bce32b41b00cfaa1045d5  build/gc4-set-contains-callback-roots.log
a9b1f06cfa96200658311d99ec3300de40d23536be80f8809ea4a74b11e6fd36  build/gc4-set-contains-semantics.log
d6bff010012f7c1088dbb401f70f79860e0820c6a2761371edb8bcaa8971824c  build/gc4-set-contains-archive-owner.log
ab8d8e3b49c1ab7132667476527b938aa588fe4bd635a9c704d9c21367a89c0f  build/gc4-set-contains-abi-gc.log
3f885b297fd6bd7cb393d09118b5946c391d00bf7be10da5440b78159ac82472  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.20 set membership callback roots.  Set mutation
and the GC4 parent remain open.
