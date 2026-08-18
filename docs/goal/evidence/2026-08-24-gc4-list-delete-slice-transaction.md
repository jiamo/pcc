# GC4 list delete-slice transaction — 2026-08-24

## Claim

C and strict pcc-Python delete-slice now split callback-capable bound
conversion from raw structural mutation:

- owner plus step/lo/hi are updateably rooted;
- step, lo and hi `__index__` conversions each execute exactly once with the
  graph unlocked;
- scalar bounds normalize against the owner length reloaded after all bound
  callbacks; and
- backend0 then uses its direct delete helpers, while backends 1-4 enter one
  graph/no-park transaction for detach, compaction and length publication.

The moving/tracing path preallocates a deletion mask, per-removed-slot split
store/decref plans, and old/new slot pairs before locking.  Under lock it
revalidates length/capacity, retargets GC4 raw-slot metadata for every compacted
survivor, detaches removed ownership, compacts and emits destination barriers,
clears the unused tail, and publishes the new length.  Every decref/finalizer/
weakref tail finishes after unlock.

## Dynamic proofs

- A compiled native/no-libpython GC4 program passes two custom bound objects.
  Their `__index__` callbacks append to the same list.  Each callback runs once
  and the deletion uses the post-callback list, matching CPython exactly:
  `[0, 1, 2, 3] -> [0, 3, 91, 93]`.
- The same program covers positive extended deletion `del ys[1:7:2]` and
  negative-step deletion `del zs[5:0:-2]`, producing `[0,2,4,6]` and `[0,2,4]`.
- A native `__del__` owns the removed terminal reference.  C and strict publish
  compacted `[0,2,3]` before the finalizer, which relocates the same list and
  appends `777`; the final list is `[0,2,3,777]`, root count is exact, and the
  GC4 old-address verifier is clean.

The initial callback regression was confirmed red as an unchanged list and
zero callback counts.  This localized the strict mirror's use of raw
`py_int_value_i64`; the accepted implementation uses the shared
`py_obj_index_i64` protocol and does not special-case the test class.

This closes delete-slice only.  Set-slice replacement snapshot/aliasing,
replacement bound callbacks, sort comparisons, constructors, C-API raw leases,
stage2 performance, fixed point and broad five-GC parity remain open.

## Gates

- C syntax threads off/on, strict `py_compile`, and strict self/no-libpython
  `py_list.py` closure: pass (one pre-existing pointer warning).
- exact `__index__` callback regression: red before, green after.
- final compiled slice mutation suite: `3 passed in 7.10s`.
- C finalizer relocation/re-entry: `1 passed in 7.83s`.
- strict finalizer relocation/re-entry: `1 passed in 133.48s`.
- full source/ABI/GC abstraction/slice neighbors: `34 passed in 17.26s`.
- task-card relocation payload/forwarding retirement gate:
  `24 passed in 138.92s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-list-delete-finalizer-final.log`
- `build/gc4-list-delete-semantics-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
8fd1790a1675ddaf98b57b73e63b26d4c8a7a780673c45a913ff4208d0420885  pcc/py_runtime/src/py_list.c
2f50a32d6554b62474454a45964cf26cfdae9910b6b7d70037cbb0e698612411  pcc/py_runtime/py/py_list.py
a1780aa9f47438a170112c3663637281cd459dc5e4c8a92d91375b3f4389fb84  tests/python/test_gc_codegen_write_barrier.py
430370836525af6649aa1903768ca5990e500d614ee4c812b1c9e98b710ec675  tests/python/test_gc_threading_substrate.py
e390ef8488a076e8b7a4cd625c1b669130f0c1964ea4c36c09c842974706c71c  tests/python/test_py_native_slice_mutation.py
a09a544042a1dc3f43cce55e4e39a02aaccd8135cbd242d9077381e5b0a7f8af  build/gc4-list-delete-finalizer-final.log
190fa91db4e8edb1ceac9badf3f58e718ab02434c39c38bd951b0f0576c171b1  build/gc4-list-delete-semantics-final.log
bc395f9989772f31349f30a621eb39eee9b47c53d33813bf73d6521d2c35792f  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.11b.1b list delete-slice.  The GC4 parent remains
`IN_PROGRESS` at set-slice replacement/aliasing.
