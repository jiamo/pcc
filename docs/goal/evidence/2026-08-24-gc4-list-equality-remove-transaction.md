# GC4 list equality and remove transaction — 2026-08-24

## Claim

The moving-backend paths for list `contains`, `index`, range-index, `count`,
and `remove` no longer retain a raw list base or borrowed candidate across
`py_obj_eq`:

- C and strict pcc-Python root the list, query and current candidate;
- the candidate load plus prepared retain occurs under the graph/no-park
  tenure, while equality itself runs after unlock;
- the owner and current length are reloaded after callback re-entry; and
- a successful `remove` opens a fresh graph transaction and removes the
  then-current numeric index, matching CPython when `__eq__` mutates the list.

The removed owned reference stays in an updateable root through owner/query
root cleanup.  Its final decref, and therefore any finalizer/weakref callback,
runs only after graph unlock.  If the equality callback clears the list and the
matched numeric index no longer exists, `remove` succeeds without a stale raw
access, matching the CPython oracle.

Backend 0 retains direct callback loops and direct removal; it does not pay the
moving-backend temporary-root or graph-transaction cost.

## Dynamic relocation and mutation proof

A real C-extension `tp_richcompare` callback explicitly relocates the list,
inserts `4242` at index zero, and returns true while `py_list_remove` is active.
Both the C runtime and the freestanding pcc-Python runtime reload the relocated
root, remove current index zero, preserve the originally compared candidate and
tail value, and return the scheduler-root count to its deliberate persistent
baseline.

This proves the equality/remove slice only.  It does not close list clear,
delete, set-slice, sort, constructor publication, C-API raw-view leases,
stage2 performance, bootstrap fixed point, or five-GC broad parity.

## Gates

- C relocation/re-entry probe: `1 passed in 0.45s`.
- strict pcc-Python relocation/re-entry probe: `1 passed in 127.87s`.
- runtime ABI/source contracts: `6 passed in 0.11s`.
- ordinary `index`/`count`/`remove`, ranged-index and dispatch parity:
  `4 passed in 26.31s`.
- C syntax with threads off/on, strict `py_compile`, and strict self-backend
  no-libpython closure: pass (one pre-existing unrelated pointer warning).
- full write-barrier source, runtime ABI chunk and GC abstraction contracts:
  `29 passed in 12.65s`.
- task-card relocation payload/forwarding retirement gate:
  `24 passed in 162.39s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-list-equality-callback-final.log`
- `build/gc4-list-equality-semantic-parity.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
20b5b6d12129322b2e42e403018036a2a6f063dd67f4c6a06fd4f1b13e9190e4  pcc/py_runtime/src/py_list.c
a100dc030b63f0079c68e4bba6859250d2754110df84cb100f7ae1ba18e61144  pcc/py_runtime/py/py_list.py
92a4327020484e0368db07dc8ab4e9c9d0371555291f1fb71747bb37987a5904  pcc/py_frontend/codegen/runtime_abi.py
88fc00ed2e63abe0cef891ce1113915b4241a04fc35916b63f4cda1038ff1e13  tests/python/test_gc_codegen_write_barrier.py
d9cef9e949efaf8530cb88c2bc32bb9fbeb0195914c8461acca7e2cdec0cd5b0  tests/python/test_gc_threading_substrate.py
2f088e7175df2abb42107dc653f26e83d8b5b7586f5e84d6ac720f812dd14d09  build/gc4-list-equality-callback-final.log
569f37948c6f63019e4dd029bccd837b2a4857ec57a24f13a3c560f2ffadfa2b  build/gc4-list-equality-semantic-parity.log
f8dd26e4b02346dd4447c49df08f4ecef96ec5595c379d93015ecd5d02842ab4  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.11b equality search and `remove`.  The parent
task remains `IN_PROGRESS`; callback-free destructive clear/delete/set-slice
transactions and their same-list finalizer re-entry proof remain open, with
sort/comparison callbacks reserved for Proposal No.11c.
