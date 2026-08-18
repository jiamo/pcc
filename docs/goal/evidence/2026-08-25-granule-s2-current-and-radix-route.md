# Current granule S2 proof and radix routing — 2026-08-25

## Claim

The strict pcc-Python runtime's S2 granule lifecycle is already production
active: ordinary object-family cells reserve non-live, publish only after header
initialization, answer through acquire reads, and retire before reuse. Exact-set
fallback remains authoritative for negative/foreign/large/raw/minor/zpage/type
and forwarding cases. Current-source real-pthread publication/lifecycle/grow,
GC3 minor retirement, GC4 ordinary-slab fallback retirement, C/strict GC0..4
provenance and layout all pass 13/13.

Historical current-design evidence also confirms the per-query chain fusion:
1.1189x over ten pairs and 1.1293x over twelve against pre-fusion, cumulative
1.157x with the earlier redundancy removal. The reciprocal candidate was
DENIED and removed.

The remaining granule data-structure owner is the hash/open-address span lookup
inside the fused predicate. It is already scoped as
`ARCH-P1-GRANULE-SPAN-LOOKUP-RADIX`. Therefore
`ARCH-P0-PROVENANCE-GRANULE-MAP` now depends on that row; after it resolves,
the parent resumes with module98 A/B and same-source Stage1/Stage2/fixed-point/
five-GC acceptance. Re-implementing S2 is not authorized.

## Gate

- granule/provenance/layout: `13 passed in 9.11s`.
- task-board validation and `git diff --check`: pass.

## Frozen identities

```text
c4e7b32aded9e2f51de602858ebcfd176ab99bd17cccc30c3449c704bcb5d88f  pcc/py_runtime/py/freestanding_allocator.py
77d82699a7751487f189de534badd5eacedc8e391edb31c68f8851cafb611d24  pcc/py_runtime/py/py_gc_backend.py
51a7bfc98b139e50cb6c6d5e66641631de6286e562928ea15df49ea6eb7ba077  pcc/py_runtime/src/py_gc_index_table.c
f5bc414ad9d5da161c24decc2c2d29789680e8bdc7bec3f6b4e75166ea0fc6bf  tests/python/test_gc_granule_map.py
b48099ad0679a35827af3a6cbb26c187d648ee522ee9a82f04e553515c559bec  tests/python/test_runtime_pointer_provenance.py
17c11e5c98c3d95c3d962785cf5dfe1ce731ca8915a36875bac7f9dbffa980a6  tests/python/test_runtime_layout_contract.py
1b5bb1873c486fabd896303a87e125ec35407146ec53134f090d9af12656005e  build/granule-s1-current-all.log
```

## Status

`DONE_STRONG` for current S2 lifecycle/fused-query prerequisite; parent stage
acceptance remains `IN_PROGRESS` behind the radix dependency.
