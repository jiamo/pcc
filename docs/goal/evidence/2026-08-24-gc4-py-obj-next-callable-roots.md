# GC4 py_obj_next internal iterator roots — 2026-08-24

## Claim

C and strict pcc-Python `py_obj_next` now root an internal `PY_TYPE_ITER`
before sequence lookup or callable-iterator callbacks.  Sequence iteration
reloads the iterator before incrementing its index and holds a heap item in an
updateable root until that write completes.

Callable iteration roots callable, sentinel, empty args and result as well as
the iterator.  It reloads all surviving values after `py_obj_call`, reloads
result/sentinel after `py_obj_eq`, and writes `PY_ITER_CALLABLE_DONE` only
through the current iterator target after equality re-entry.  Every failure,
StopIteration and returned-result path balances its handles before decref or
return.

## Dynamic proof

C and strict probes construct the real two-argument `iter(callable, sentinel)`
runtime state.  The first callable result and sentinel are distinct
C-extension objects, so `py_obj_eq` invokes their `tp_richcompare`.  That
callback directly relocates the iterator.  After callback return the first
item is returned, the second call observes the sentinel and publishes DONE,
and a third call repeats StopIteration without calling the callable again.
Only the two external probe roots remain, then both are explicitly removed.

The C-extension result/sentinel objects are nonmoving by contract; their
handles prove lifetime/balance across callback re-entry, not relocation.  A
discarded ordinary-user-instance control recorded zero `__eq__` callback hits,
matching the runtime's current comparison capability boundary.

## Gates

- static C/strict contract plus C direct-relocation probe:
  `2 passed in 0.22s`.
- strict direct-relocation probe: `1 passed in 139.13s`.
- callable-sentinel, builtin-next and sequence/comprehension iterator
  semantics: `15 passed in 37.32s`.
- strict no-libpython source closure and C syntax: pass.
- strict archive owner: `1 passed in 142.23s`.
- runtime ABI chunk plus GC abstraction: `17 passed in 10.45s`.
- task relocation payload/forwarding retirement gate: `24 passed in 14.78s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-py-obj-next-source-c-oracle.log`
- `build/gc4-py-obj-next-callable-roots.log`
- `build/gc4-py-obj-next-semantics.log`
- `build/gc4-py-obj-next-archive-owner.log`
- `build/gc4-py-obj-next-abi-gc.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
cb11ae7bbd474563f8df35bbd3736ad5ef4dabe3c1f3f89b5cf8748d488664fb  pcc/py_runtime/src/py_iter.c
a8d513f5de42c54f5ab1f531b1907ee2cecaae4c9896dd55f2355bd025718b6f  pcc/py_runtime/py/py_iter.py
eca2ecb5acf3b268c93f1887b954d90bb7115663d0fc6e3fcba3e1743b8753e0  tests/python/test_gc_threading_substrate.py
6e386fdeb1aa1f43396f485b28b580d1cfef688207e7946367266ca33310c3b9  build/gc4-py-obj-next-source-c-oracle.log
a3a66d1027f90fcd4934fbd8fc2039736b698ae8f105ec7ea8fbca38480b4cc4  build/gc4-py-obj-next-callable-roots.log
27f271f6888095c97815da0aa1da736673a7e43c520183e13a49d14bda78fed9  build/gc4-py-obj-next-semantics.log
ee71b5b01a0c8ef5db9cfc0f7aca56163ff338c0f3d4a2b17c5570b4796c57ed  build/gc4-py-obj-next-archive-owner.log
e9f49ec7643303b54c30e01817a1864e9291eec1bb6360b0503fe34bc3bcacfc  build/gc4-py-obj-next-abi-gc.log
36a439667dc3027f39faa4112cfd7f60b8363314daec3a0bb15ca5d5a8339ab0  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.17 internal sequence/callable iterator roots.
The GC4 parent remains `IN_PROGRESS`.
