# GC4 set remove split commit — 2026-08-25

## Claim

C and strict pcc-Python `py_set_remove` now reuse the rooted/restartable
hash/equality lookup.  After a stable match they initialize a store plan,
reacquire graph lock, revalidate owner/table/slot, commit key->dummy and size--
together, unlock, then finish the plan.  Old-key decref/finalizer work cannot
run while the set publishes half of the deletion.

The strict C-API managed-dealloc flag was also corrected from the private,
wrong `0x1000000` bit to the public fake-`Python.h`/C-oracle ABI `1<<62` in the
dealloc owner and the contextvar/seqiter/slice type owners.

## Dynamic proof and open boundary

Both mirrors perform a real equality callback that relocates the set, restart
the lookup, remove the equal stored key, expose len zero / membership false,
and complete two forwarding retirement epochs with balanced roots.

The C oracle then releases the forwarding source's final C-extension key
ownership exactly once; its managed-dealloc hook observes len zero and
membership false.  Strict independently proves the corrected bit62 managed
dealloc ABI with a direct spare-object control, but after the relocated-set
retirement still records zero stored-key dealloc callbacks.  That is an open
strict forwarding-source C-extension payload-release gap routed to
`GC-P0-FORWARDED-SOURCE-PAYLOAD-RETIREMENT`; it is not hidden as green parity.

The repository's higher-level native-extension managed-dealloc test remains
blocked earlier by the known self-link native-extension export-anchor boundary.

## Gates

- source/order/bit62 contracts plus C full callback/finalizer proof:
  `4 passed in 0.19s`.
- C/strict split-commit/retirement probes with explicit strict release-boundary
  assertion: `2 passed in 1.24s`.
- set remove/discard semantics: `2 passed in 1.88s`.
- strict closures for set + four C-API owners and C syntax: pass.
- strict archive owner: `1 passed in 0.97s`.
- runtime ABI chunk plus GC abstraction: `17 passed in 11.20s`.
- task relocation payload/forwarding retirement gate: `24 passed in 8.23s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-set-remove-source-c-oracle.log`
- `build/gc4-set-remove-split-commit.log`
- `build/gc4-set-remove-semantics.log`
- `build/gc4-set-remove-archive-owner.log`
- `build/gc4-set-remove-abi-gc.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
0d488c31c6b8896a9d60b0c021954b7db66fb6a1fe3ac82f8e16737bb00577b8  pcc/py_runtime/src/py_set.c
82d2f76c267bd0e405e459e5e2574f15dfe696a2202a4d2fd40e83642b4a6feb  pcc/py_runtime/py/py_set.py
9d6d23efef4ea8a7b34c692ff244f875b4479e6c8fe791b199abe95db15e6140  pcc/py_runtime/py/py_capi_cext_runtime.py
73ac6a0b0080038a434c79a9a583e20b89d5cdfe5342c6c7ffa6bcdd1a0681b5  pcc/py_runtime/py/py_capi_contextvar_runtime.py
af1cbf0e765e5debcac5a1f6ff796c787fd2f53779f78a32129b750955481d0b  pcc/py_runtime/py/py_capi_seqiter_runtime.py
d315ca135316d56f16ab4b25309d6642b797c3ee876bdfcffbcf4f08c5b28221  pcc/py_runtime/py/py_capi_slice_runtime.py
108f29008802e9acb56ba79bd6040e0a7528e7931e67a6539824821a16b849f1  tests/python/test_gc_threading_substrate.py
efef0d405bb977e9f974dc1ea8def6a64cf0f7c904da7c41c280c5d4a2243edc  build/gc4-set-remove-source-c-oracle.log
77e4c36f44b5a4046b75c63797906cd9364059d5a056c2a231eebab704356171  build/gc4-set-remove-split-commit.log
53df1a2b169a2c701f34c023153ba3f392fda36337c9eed08b2fb6c0488fe8de  build/gc4-set-remove-semantics.log
0de3c66c929c4e68b1e0fb624529b0c051c7193e66ce3880debb5931677fd26f  build/gc4-set-remove-archive-owner.log
dde0dc4c63e10563fafc15a16a738d90cfe7402dff2304c1667543b40ee93d2a  build/gc4-set-remove-abi-gc.log
72d9f144b0434cf6417f257f4de00a9dac078826a830604383578ed1f2d10c6c  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for set target-side remove commit/order and the bit62 ABI repair.
Strict forwarding-source C-extension final release remains explicitly open.
