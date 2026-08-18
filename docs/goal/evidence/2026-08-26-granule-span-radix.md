# Granule span lookup radix

Date: 2026-08-26  
Task: `ARCH-P1-GRANULE-SPAN-LOOKUP-RADIX`

## Implementation

The strict pcc-Python allocator now publishes an immortal four-level radix of
4096 pointer slots per level.  A 4 KiB granule key is split into four 12-bit
indexes, covering allocator addresses below 2^60; unsupported higher addresses
fail slab registration into the existing raw fallback rather than producing an
inexact answer.

Each node is one zeroed/accounted 32 KiB metadata mapping, exactly 4096
eight-byte slots.  Under the allocator's
single-writer lock, registration preflights duplicates and hash capacity,
allocates every missing radix node, then allocates the immutable span.  Only
after all failure points pass does it release-publish the same span into all
sixteen leaf slots.  Readers acquire-load root, three child/leaf pointers and
the exact span.  Empty immortal internal nodes may remain after allocation
failure, but no leaf mapping is partially visible.

The old hash registration remains as a cold compatibility oracle/ABI during
the parent granule-map migration.  Production `pcc_gc_granule_span` and the
fused objecthood predicate no longer hash, compare or probe.

## Correctness

The real-pthread test's old hash-only metadata formula initially failed with
exactly six additional radix nodes (64 KiB in the first candidate, later
right-sized to 32 KiB).  It now checks the exact sum of hash
snapshot generations, span arenas, and exported radix-node count.  Per-call
registration deltas include each actual node publication; ordinary/grow
overlaps and the permanent-negative sentinel remain mandatory.

```text
strict no-libpython allocator closure                 rc=0
granule/provenance/layout                             13 passed in 9.09s
GC0 finalizer/weakref/resurrection/trashcan           44 passed in 84.98s
GC1                                                    44 passed in 55.90s
GC2                                                    44 passed in 56.60s
GC3                                                    44 passed in 55.54s
GC4                                                    44 passed in 61.50s
```

The 13-item gate includes raw/object/foreign/interior behavior, sixteen-key
growth, live-header publication, real pthread lifecycle/grow races, GC3 minor
source retirement, GC4 fallback-tail retirement, C/strict GC0..4 provenance
and layout parity.

## Matched A/B

An attempted two-archive control was rejected because mixing an old allocator
query source with the current aggregate closure did not compile.  It is not
used as evidence.

The valid experiment used one instrumented runtime archive containing both
fused query bodies and a startup-only selector.  Hash and radix benchmark
sources differed only in setter literal 0/1; both arms paid the identical mode
load/branch, allocator registration, radix/hash metadata and machine image.
The selector and hash branch were removed from production after measurement.

Original 400-round `benchmarks/python/granule_heavy_object.py` output was
`19206400000` in every run.  One warmup per arm and ten alternating pairs:

```text
pairs favouring radix           9 / 10
median wall speedup             1.03803x
median CPU speedup              1.03661x
candidate/base instructions     0.97184
candidate/base cycles           0.96690
candidate/base footprint        0.99771
```

Manifest: `build/granule-radix-query-ab-v1/manifest.json`.

## Current profile

For a profile-only copy with the identical operation mix and 1200 rounds, the
pure production radix captured 3360 self samples.  The entire fused
`pcc_gc_granule_is_object_start` predicate is 343 samples / 10.21%; the
decomposed radix span helper is 22 / 0.65%.  `_granule_hash` and
`_granule_find_slot` are absent from the hot query profile.  Thus the complete
fused predicate is now below the old span-lookup-only 12.2% baseline.

Artifacts:
`build/granule-radix-final-heavy-profile.folded` and `.svg`.

## Claim boundary

`DONE_STRONG` for replacing/validating the strict allocator's hot span lookup.
This is not a Stage2, module98, Stage1, fixed-point or five-GC bootstrap-matrix
performance claim.  Those remain with the parent rows, which must re-profile
their own workload before paying for a full cold build.
