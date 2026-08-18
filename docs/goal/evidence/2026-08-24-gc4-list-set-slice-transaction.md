# GC4 list set-slice whole-payload transaction — 2026-08-24

## Claim

C and strict pcc-Python set-slice now use an independent replacement snapshot
and one auditable whole-payload publication:

- owner, replacement and step/lo/hi stay updateably rooted;
- each bound `__index__` conversion runs exactly once while graph-unlocked;
- the replacement is copied after bound callbacks, so `lst[...]=lst` and
  overlapping aliases read a stable post-callback snapshot;
- extended-slice length mismatch returns before any destination mutation; and
- the final list is built in a fresh items buffer, then published atomically as
  base/capacity/length before any old-reference finalizer tail.

For backends 1-4, the transaction preallocates final-value retain plans,
old-slot split store/decref plans and old/new slot mappings.  Locked commit
revalidates owner and snapshot shapes, retargets GC4 raw-payload metadata,
retains every final value, detaches every old ownership, emits new-slot
barriers and publishes the new payload.  Retain/decref tails and old-buffer
free run after unlock.  Backend0 performs the same final-buffer semantics with
direct owned `py_list_get` references and publishes before direct old decrefs,
without graph locks or GC plans.

## Dynamic proofs

- Confirmed red self assignment produced `[0,0,0,0,0,3]`; the final result is
  CPython-compatible `[0,0,1,2,3,3]`.
- Confirmed red custom bounds were never called and appended replacement at the
  tail.  Final GC4 behavior calls lo/hi exactly once and produces
  `[0,7,8,3,91,93]` after their same-list append callbacks.
- Tuple replacement grows a slice to `[0,8,9,10,3]`; negative-step extended
  assignment produces `[0,7,2,8,4,9]`; empty-range insertion preserves the
  correct tail.
- A native removed-value `__del__` sees published length five, relocates the
  same list and appends `777`.  C and strict finish as `[0,7,8,2,3,777]` with
  exact roots and no old addresses.
- Extended replacement length mismatch returns `-1` and leaves every element
  unchanged before the finalizer probe's cleanup.

This closes list set-slice.  Sort/comparison callbacks, constructor
publication, C-API raw-view leases, callbacks outside list, resurrection,
stage2 performance, fixed point and broad five-GC parity remain open.

## Gates

- C syntax threads off/on, strict `py_compile`, and strict self/no-libpython
  set-slice closure: pass (one pre-existing pointer warning).
- final set/delete/slice semantics: `4 passed in 30.34s`.
- C/strict finalizer and mismatch transaction: `2 passed in 133.97s`.
- full source/ABI/GC abstraction neighbors: `32 passed in 10.40s`.
- task-card relocation payload/forwarding retirement gate:
  `24 passed in 137.96s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-list-set-slice-finalizer-final.log`
- `build/gc4-list-set-slice-semantics-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
3fb5d9ec29928878576d80552bbfa879e75451078ed3f6852d5ab5ad1a269ca2  pcc/py_runtime/src/py_list.c
2f50a32d6554b62474454a45964cf26cfdae9910b6b7d70037cbb0e698612411  pcc/py_runtime/py/py_list.py
438f1efb1b3b5dae8e72d3fb0670d9a04ae3ed06c6ab27a8ca323fab8e4c55e1  pcc/py_runtime/py/py_list_set_slice.py
9999ffe0ed2497435db0725e4235f8494b8a062e7599a6bd00bc453f24971888  tests/python/test_gc_codegen_write_barrier.py
34c264587a1571b9b3a1d2cf50bbf21798277f6c9786b09172f993590bd83979  tests/python/test_gc_threading_substrate.py
e3b983d07fb00195f81328d480f4f0c4e9171e35d74e07397f9577f037b65e09  tests/python/test_py_native_slice_mutation.py
f8a8b7270d29b9bc69b790ca3142aa0bcac6d4fec0f1368d1ae5b34448cd70c6  build/gc4-list-set-slice-finalizer-final.log
142925d4e4deeffd5762095c692f51bdc12b57cf77ab70ab08ef4cfa0ac83ca2  build/gc4-list-set-slice-semantics-final.log
bea6ddaf4ca86183ff8680d5cef813d9f2c356dc19f2fa3aa44361b42dcd6207  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.11b.2 list set-slice.  The GC4 parent remains
`IN_PROGRESS` at sort/comparison callbacks.
