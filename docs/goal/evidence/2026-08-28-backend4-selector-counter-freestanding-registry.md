# Backend-4 selector counter freestanding registry repair

## Boundary

The current `freestanding_gc_relocation_selector.py` already defined two raw
i64 diagnostic counters for the FRESH_ALLOC filter disagreement, but their
exact names were absent from the finite freestanding GC-global registry.  A
fresh runtime build therefore failed closed on the emitted
`bitcast ptr @pcc_gc_backend4_*_g to ptr`; no current-source Stage1 could use a
provenance-matching runtime archive.

## Fix

Register only
`pcc_gc_backend4_candidate_fresh_skips_g` and
`pcc_gc_backend4_relocation_add_refusals_g` in
`FREESTANDING_GC_I64_GLOBALS`.  The selector closure test now distinguishes
these two globals defined by the strict object from its finite undefined raw
global imports and requires the object to own both globals plus both exported
counter functions.  Arbitrary `pcc_gc_*` references remain rejected.

## Evidence

```text
selector source/LLVM/self closure subset                  4 passed
complete selector closure + isolated archive owner gate   5 passed in 160.69s
forced current-compiler runtime rebuild                    186 objects
runtime codegen checksum distribution                      186 x 77ca361c...
runtime archive provenance verify                          passed
archive SHA-256                                            f17a0d99...
current-source strict Stage1 v29                           257.16s; libSystem only
```

The repair does not change the FRESH_ALLOC predicate or relocation-set
semantics; it restores the intended exact unsafe-global ownership boundary.
The full GC4 behavioral/bootstrap gate remains deferred with the other GC
transfer work, so this slice is `DONE_WEAK`, not cross-GC completion.
