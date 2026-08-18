# GC4 list sort callback transaction — 2026-08-24

## Claim

Every frontend-owned `list.sort` path now uses one receiver transaction:

1. copy the original elements to a hidden working list;
2. clear the receiver before any key/comparison callback, matching CPython's
   observable empty-list boundary;
3. sort only the working list (custom static `__lt__`, key transform, ordinary
   runtime order and reverse post-pass all share this shape);
4. detect receiver mutation from its post-callback length;
5. atomically replace the receiver through the proven set-slice transaction;
   and
6. if mutation occurred, raise `ValueError("list modified during sort")` after
   the sorted original elements are published.

The hidden working list is an owned object handle; generated code never caches
its raw items base through a callback.  Each read/write goes through the
transactional list runtime, which resolves forwarding on entry.  An attempted
LIFO temp-root wrapper was rejected by precise stack-map analysis because the
comparison exception edge joined `try.err` with mismatched root state; that
proposal was removed rather than weakening stack-map checks.

## Dynamic proofs

- Before the change, a custom `__lt__` callback appended three `99` elements,
  returned without error and left `[1,2,3,99,99,99]`.  Under GC4 the final
  behavior is CPython-compatible: it raises `ValueError: list modified during
  sort` and publishes `[1,2,3]`.
- A key callable that appends to the same receiver produces the same exact
  mutation error and sorted-original result under GC4.
- Custom `sorted(...)` remains non-mutating, primitive and reverse sorting
  retain their results, key sorting remains stable and its Schwartzian
  transform remains O(n log n).

This closes frontend `list.sort` callback visibility/publication.  It does not
claim that the general `py_obj_sorted` implementation roots arbitrary opaque
iterators or C-extension comparator callbacks internally; that is explicitly
part of the remaining callbacks-beyond-list boundary.

## Gates

- final custom/key/ordinary/reverse and source-order neighborhood:
  `22 passed in 47.42s`.
- strict Python syntax: pass.
- bootstrap baseline: `2 passed, 2 deselected in 0.74s`.
- fallback and IR fallback ratchets: `40 passed in 486.66s`.
- current runtime identity retains the immediately preceding set-slice
  relocation/retirement gate: `24 passed in 137.96s`; no runtime source changed
  in this sort slice.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-list-sort-final.log`
- `build/gc4-list-sort-bootstrap-baseline.log`
- `build/gc4-list-sort-fallback-baseline.log`
- `build/gc4-relocation-mutator-quiescence.log` (unchanged runtime identity)

## Frozen identities

```text
ee2770fd35271e3129d2dc075783e9259f867abfb13326f9d31197b320ccc512  pcc/py_frontend/codegen/list_method_lowering.py
dea8b8ab7dd058c25f83b42acfc22a8d540039cdcedce05324835d78795f2a52  tests/python/test_native_sorted_custom_lt.py
641752c219183ae692ad3875f5fd57d5321cd33a87571a7f2a0cf47ca8808aea  tests/python/test_native_dynamic_list_sort_callable.py
1d78a42e1c3c57479f9bfca2b4d441e839387b062b33ada691b924041d623411  tests/python/test_native_sorted_key_lambda.py
13c3c796935eef1c856c781f0ef45bfa74c9287b1821ae869a0a6ddb7748fead  build/gc4-list-sort-final.log
5ecf302bb09328d8aac5763b32a396fbc00a537ff6cbe7e14ee9d41c0ee434d7  build/gc4-list-sort-bootstrap-baseline.log
250a6efc38ca1ff0d5b93cc2cdadb779fc435ad852494d8d9d2016c4a82b5195  build/gc4-list-sort-fallback-baseline.log
bea6ddaf4ca86183ff8680d5cef813d9f2c356dc19f2fa3aa44361b42dcd6207  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.11c frontend list-sort callback semantics.  The
GC4 parent remains `IN_PROGRESS` at constructor publication and non-list
callback/C-API boundaries.
