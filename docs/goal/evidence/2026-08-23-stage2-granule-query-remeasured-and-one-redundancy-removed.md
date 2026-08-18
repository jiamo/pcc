# Stage2: the granule provenance query re-measured, and one per-query redundancy removed

Date: 2026-08-23

Rows: `ARCH-P0-PROVENANCE-GRANULE-MAP`, `PERF-P0-PCC1-BOOTSTRAP-BEATS-HOST`
(route)

Status: measurement + one accepted micro-slice. No stage1/stage2 timing, no
module98 A/B, no fixed point.

## Why re-measure before optimizing

The routed baselines for this row are "index machinery 21.26% (frozen module98
worker) / 18.98% (live 600-fn compile)". Those predate the S2 activation that
has since landed in the strict port, so they describe a runtime that no longer
exists. Any A/B bar quoted against them would be measuring the wrong thing.

## What the current runtime actually spends

Workload: a natively compiled pcc-Python program doing heavy object work —
4,000-node linked lists rebuilt 40 times, six method-dispatch walks per
build, plus 4,000 dict inserts of fresh instances per round. 5.9 s wall,
sampled 4 s with `sample`, aggregated with
`scripts/pcc_sample_aggregate.py` (3,195 self samples).

```text
categories
  other               1648  51.6%
  gc_other            1180  36.9%
  gc_read_barrier       154   4.8%
  class_lookup           86   2.7%
  allocator              70   2.2%
  gc_index               57   1.8%

top symbols
  305  pcc_allocator_granule_object_slot
  195  pcc_allocator_granule_find_slot
  194  pcc_gc_granule_span
  100  pcc_allocator_granule_stride_count
   79  pcc_gc_pointer_is_managed
   73  pcc_gc_granule_is_object_start
   73  pcc_gc_load_ptr
```

Two findings, both load-bearing for stage2:

1. **S2 did what it claimed.** The old top leaf
   `pcc_gc_managed_pointer_find_slot` no longer appears; the whole `gc_index`
   category is 1.8%. Per-object exact-set traffic for allocator-owned objects
   is gone.

2. **The replacement costs more than what it replaced.** The granule query
   path — `granule_object_slot` + `granule_find_slot` + `granule_span` +
   `granule_stride_count` + `granule_is_object_start` — is 867 of 3,195
   samples, **27.1%**, against the 21.26% the exact set cost in the frozen
   emit worker. That is the direct reason the stage2 ratio has not moved: the
   provenance tax was relocated, not removed.

The mechanism is visible in source. Every objecthood question runs
`pcc_gc_granule_span` (acquire-load the table pointer, `addr >> 12`, hash,
open-addressing probe), then `_granule_object_slot` re-derives and re-validates
the slab carve, then one atomic acquire load of the live magic. The design
document's own references are one shift plus one load
(ZGC `ZPageTable`, Go `spanOf`), and it pre-registered the escape hatch:
"v2 may move to a two-level radix if the probe still shows." It shows —
`granule_find_slot` + `granule_span` alone are 12.2%.

## The one thing changed here

`_granule_object_slot` recomputed the slab's carve count from the 11-entry
size-class chain on every query, purely to compare it against the count the
span descriptor already stores at offset 24:

```python
    count: i64 = _granule_stride_count(stride)
    if count == 0 or load_i64(span, 24) != count:
        return null()
```

The span descriptor is immortal and write-once, `_span_new` stores that same
count at registration, and `_granule_register_slab_locked` already refuses a
stride whose count is 0 — before the granule key is release-published, so any
acquire-side reader that observes the key observes a validated
`(stride, count)` pair. The recomputation cross-checked exactly one field
while `kind`, `stride` and `base` were already trusted from the same record.
It now reads the cached count and requires `count > 0 and stride > 0`; the
validation lives once, at registration.

Only the strict port changed. The C side is an honest stub
(`py_gc_index_table.c:1262` returns `-1`, cc-mode has no slab lifecycle), so
there is no C mirror of this query to keep in step.

## Measured

Both arms are the same workload source compiled against runtime archives that
differ only by the change above. Two discarded warmups per arm, then ten
alternating pairs (`BC/CB/...`), wall clock per process:

```text
pair  base   cand   C/B
   1  5.963  5.855 0.9819
   2  6.223  5.736 0.9217
   3  5.943  5.941 0.9997
   4  6.084  5.736 0.9428
   5  5.927  5.806 0.9795
   6  6.023  5.940 0.9861
   7  5.922  5.841 0.9864
   8  6.208  5.848 0.9420
   9  5.989  5.828 0.9730
  10  5.978  5.786 0.9678

base median        5.984
cand median        5.834
paired-median C/B  0.9762   =>  1.0243x
```

Ten of ten pairs favour the candidate, which is what makes this separable from
noise; a first 7-run unpaired attempt read 5.90 vs 5.85 and was correctly
inconclusive. The size is consistent with the 3.1% leaf share it removes.

## Gates

```text
tests/python/test_gc_granule_map.py + test_runtime_pointer_provenance.py
  + test_runtime_layout_contract.py                13 passed in 303.29s
no-libpython closure emit of freestanding_allocator.py    exit 0
five-backend finalizer/resurrection/weakref/trashcan
  backend 0  44 passed 94.43s     backend 3  44 passed 90.91s
  backend 1  44 passed 91.90s     backend 4  44 passed 93.08s
  backend 2  44 passed 91.34s
tests/python/test_bootstrap_gate_baseline.py       2 passed, 2 deselected
```

## Update 2026-08-24 — the chain was fused, and a reciprocal was denied

The 27.1% was call depth, not data structure. One provenance question ran five
pcc-compiled calls (`pcc_gc_pointer_is_managed` -> `is_object_start` ->
`_granule_object_slot` -> `granule_span` -> `_granule_hash`/`_granule_find_slot`),
each paying frame and root bookkeeping for a leaf that is a shift, a probe and
a few compares. `pcc_gc_granule_is_object_start` now performs the whole chain
as straight-line code, with every check in the same order; the decomposed
helpers stay exported and unchanged for their own callers and the focused
tests.

```text
fusion vs the redundancy-removal arm
  10 pairs, 10 favouring candidate, paired-median C/B 0.8937  -> 1.1189x
accepted state vs the pre-fusion arm
  12 pairs, 12 favouring candidate, paired-median C/B 0.8855  -> 1.1293x
cumulative on this workload                0.9762 x 0.8855 = 0.8645 -> 1.157x
```

Re-profiled after the fusion: the predicate is a single leaf at 458/3224 =
14.2% and the whole provenance path is 16.1%, against 27.1% at the start.

A cached `ceil(2**32/stride)` reciprocal replacing the remaining division was
verified exhaustively over its real domain (11 strides x every carve offset in
`[0, 65488)`, largest product 42 bits) and then measured: 12 of 24 alternating
pairs, paired-median 1.4% against it. `[DENIED]`, removed by forward patch with
a comment at the site, descriptor back to 32 bytes, its focused test removed
with it.

Gates re-run on the accepted state: granule/provenance/layout 13 passed,
closure emit exit 0, five-backend finalizer/resurrection/weakref/trashcan 44
passed on each of 0..4, bootstrap gate baseline 2 passed. One batched granule
run reported the pthread grow-race failing; it passed in isolation and on a
warm re-run, and was archive-rebuild contention inside the batch.

## Next owner, stated so it is not re-derived

The remaining measured owner inside this path is the span lookup itself
(12.2%): a hash plus open-addressing probe per provenance question, where the
reference designs do one shift and one load. That is the pre-registered v2
two-level radix, now tracked as `ARCH-P1-GRANULE-SPAN-LOOKUP-RADIX` and wired
as a dependency of the stage2 headline row. A 1.10x module98 bar cannot be
claimed for the granule row while its own query path costs more than the
mechanism it replaced.

## Nonclaims

This is a runtime-workload measurement on one machine, not a pcc1 profile, not
a stage1 or stage2 timing, and not the frozen module98 A/B. It says nothing
about the S3 bar. The 27.1% figure is from this workload's sample mix and is
not a claim about the stage2 emit worker, whose own share must be re-measured
on a current-source pcc1.
