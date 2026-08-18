# Packed instruction and relocation plane

## Claim

The finite AArch64 instruction transport is closed for the retained Stage2
closure: every normal emitter-owned instruction is represented by packed
scalar records, and the diagnostic text assembler remains an exact oracle.
The native-object writer also batches each section's relocation records into
one compiler arena on pcc1 instead of materialising one `bytes` object per
relocation.  This is an accepted representation and worker-performance slice;
it is not a whole-Stage2 or fixed-point claim.

## Inventory and exact output

- `build/structured-inventory-packed-v1.json`: 226 frozen v51 sidecars,
  21,124,702 structured instructions and **zero** fallback instructions.
- `build/structured-representative-pco-v53/process.result.json`: COMPLETE in
  63.064s under the 8 GiB circuit breaker.  Source indices 96, 79, 5, 86, 90
  and 1 cover tiny, `py_ast`, medium, heavy and worst retained modules.  Every
  PCO is byte-identical to the existing v51 PCO or to the PCO assembled from
  its retained diagnostic `.s` oracle.
- `assemble_lines` now remaps the one-shot instruction arena's line-index
  column in place.  It no longer copies all four scalar columns through a
  physical-record arena and then a per-section record arena.

Representative exact PCO SHA-256 values:

```text
96 47c3d22f47490d47cd6e48674290873f735e1e6c6c34e4a88874e9b189a0e8d0
79 0ead76f72c90f479810e9c504de5f1b8021cddee7e3612ec983cf94916c19fb0
5  bbfff8731c4dd6e2a583bedb9aef897f2d50d1aff2a2fde9e13eb082793ca749
86 370475fb32c0fd89232eb3c7a31213be1b6da2dd52e6726ffcd0f76f667a902f
90 b7ac3ba17f97231f8f0704b0f6932e510c81d1e2ce4b8daaa1a056d1dac498f8
1  ba520dc2cf090ccbe0d2a0ed0d5a1cf69903b14c9450013e61653819b1abd7da
```

## pcc1 measurements

The first packed-word/remap build, v53 (`a45952d8...`), was deliberately
measured before adding another candidate.  Against v52 on the identical
`module_79.direct.pidx`, it was byte-identical but only reduced instructions
0.54% and tree RSS about 1.1%; that result closes required representation debt
but is not presented as the Stage2 speed solution.

The next bounded owner was native-object encoding.  `py_ast` has 69,733
relocations but only a 5.3 MiB final PCO.  v54 (`9f21db47...`) replaces the
69,733 per-relocation `struct.pack`/`bytes` temporaries with one native scalar
arena and one packed relocation blob.  One adjacent v53 -> v54 pair on the
same sidecar produced:

```text
metric                    v53 control          v54 candidate       C/B
wall                      19.72s               18.04s               0.915
user+system               19.59s               17.99s               0.918
instructions              296,458,591,698      274,576,026,680      0.926
cycles                     65,298,253,572       59,892,925,085      0.917
process-tree RSS           1,817,460,736 B      1,681,031,168 B      0.925
peak footprint             1,798,260,464 B      1,663,108,752 B      0.925
PCO SHA-256                0ead76f7...          0ead76f7...          exact
```

The slice therefore gives about 1.09x worker CPU/wall and 7.5% lower RSS on
the representative input.  It is accepted independently of Stage1: v54
Stage1 was 164.45s / 675.15 tree CPU seconds / 4.826 GiB sampled tree peak,
with a runnable libSystem-only pcc1.  v53 was 169.16s / 673.09 CPU seconds, so
Stage1 compute is effectively unchanged.

## Remaining memory owner

The v54 native debug counters provide the next architectural boundary:

```text
phase                 RSS          live allocator bytes   mapped capacity
decode complete       154.7 MB      80.8 MB                132.6 MB
transport complete    1,173.1 MB   555.2 MB              1,143.2 MB
assemble complete     1,329.0 MB   630.4 MB              1,298.2 MB
encode complete       1,682.4 MB   768.2 MB              1,649.1 MB
```

The final 1.68 GiB contains only 768 MiB of live allocator requests; about
881 MiB is retained capacity/fragmentation caused by object-shaped temporary
construction.  Backend transport is the largest growth step (+1.02 GiB RSS),
and native-object encode remains second (+353 MiB) after the accepted batch.
The next slice must reduce those live/object projections or close their
phase overlap.  Scheduler tuning cannot solve this owner, and another full
Stage2 is not justified until a representative worker shows a material
additional RSS/CPU reduction.

## Gates

```text
91 passed, 12 deselected  arm64 structured/encode/driver/native-object gates
14 passed                 record and instruction inventory tool gates
1 passed in 43.15s        complete contextual stage1 closure (227 modules)
92 passed                 relocation plus structured/driver focused gates
90 passed                 final codec/native-object/structured focused gates
1 passed in 43.12s        final contextual stage1 closure (227 modules)
```

All pytest commands used `-x -n0`.  Long compiles and worker measurements used
the repository process-tree sampler/performance lock and hard 8 GiB or 4 GiB
RSS limits.  No Stage2, GC, fixed-point or generic Python tuple-JSON claim is
made here.
