# GC4 tuple method callback roots — 2026-08-24

## Claim

C oracle and strict pcc-Python tuple `count`, `index` and `index_range` now
share one rooted scan.  It registers the tuple owner and query, treats every
`py_tuple_get` result as owned, roots that element across `py_obj_eq`, reloads
all surviving values after callback return, then detaches and decrefs the
element.  This also fixes the C oracle's prior one-owned-reference leak per
examined element.

Range normalization and Python ValueError behavior remain unchanged.  This
claim does not include dict/set hash/equality loops.

## Dynamic proof

C and strict probes build a two-element tuple of C-extension equality objects.
The first real `tp_richcompare` callback directly relocates the tuple.  The
same current tuple then produces count `2`, index `0`, and ranged index `1`;
four equality callbacks execute in total and only the single external probe
root remains before cleanup.

## Gates

- static shared-scan contract plus C relocation probe: `2 passed in 0.23s`.
- strict relocation probe: `1 passed in 144.89s`.
- tuple count/index/index-range semantics: `2 passed in 32.66s`.
- strict no-libpython source closure and C syntax: pass.
- strict archive owner: `1 passed in 141.99s`.
- runtime ABI chunk plus GC abstraction: `17 passed in 10.75s`.
- task relocation payload/forwarding retirement gate: `24 passed in 14.74s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-tuple-method-source-c-oracle.log`
- `build/gc4-tuple-method-callback-roots.log`
- `build/gc4-tuple-method-semantics.log`
- `build/gc4-tuple-method-archive-owner.log`
- `build/gc4-tuple-method-abi-gc.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
60be553cf5678b48cf1f4ea30601416b3e6380790e671ec24dcbaaf3730daaf1  pcc/py_runtime/src/py_tuple_methods.c
2be954228573546c0bf1edbde4cc7c20e9f5faf848e98505c70b24834e00cb5b  pcc/py_runtime/py/py_tuple.py
516764a594de090ba047ae843b2f43b2d271b64e9c409cd3d7242dfb55512ab4  tests/python/test_gc_threading_substrate.py
cc1314e014c826f2466783519218ef98f17a112afd022b3f195d7e4acaf83583  build/gc4-tuple-method-source-c-oracle.log
b76f6a60e7cdb09d37ae3a52f1c6d106a9e3171373cf98b8cd04481dcbbdb4b8  build/gc4-tuple-method-callback-roots.log
cff49605ad2f670ae5c97bedcb44d5aa419f9057a8a39a26dd94ca89058b2704  build/gc4-tuple-method-semantics.log
7949a878e5e08452bd638daba35453eccee64100e6ac32cea2b1231c69898e4c  build/gc4-tuple-method-archive-owner.log
3fd08d628b2f5c823855f1b8ff4769ab399e09424573b241a33c591fe7c363ed  build/gc4-tuple-method-abi-gc.log
e9c051395a344f0b1bbbfb579b3386681a5367dc41695c4309e4f665b601bb49  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.18 tuple equality scans.  The GC4 parent remains
`IN_PROGRESS`.
