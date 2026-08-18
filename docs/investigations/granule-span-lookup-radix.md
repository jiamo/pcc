# Granule span lookup radix

## Goal

Replace the hot 4 KiB-granule hash/open-address probe with an address-indexed
shift-and-load radix while preserving the current S2 exact-provenance,
publication, transactional registration, telemetry, moving-GC retirement and
five-backend contracts.  Predecessor evidence is
`pcc1-stage2-emit-throughput-and-memory.md` Updates No.54/64 and
`docs/goal/evidence/2026-08-25-granule-s2-current-and-radix-route.md`.

## Mode

Continue mode.  Current task-board owner:
`ARCH-P1-GRANULE-SPAN-LOOKUP-RADIX`.

## Current reproducer and baseline `[CONFIRMED]`

`benchmarks/python/granule_heavy_object.py` is the retained query workload.
After call-chain fusion, the whole provenance path was 16.1% and the remaining
hash/span lookup was historically 12.2%.  The current Stage2 rank-1 medium
worker independently attributes 18.89% of all self samples to the fused
granule-object-start leaf across compiler owners.  These shares are routing
signals; only a current alternating workload A/B may accept the radix.

## Reference facts

- OpenJDK `ZGranuleMap` uses a flat mmap-backed array indexed by
  `offset >> ZGranuleSizeShift`; page publication precedes the table store and
  acquire/release variants exist.  pcc cannot copy the flat shape because its
  allocator receives sparse host virtual addresses.
- Go's `mheap.arenas` is an address-indexed L1/L2 map.  L2 pointers publish
  nil -> non-nil and old backing is never freed; readers are lock-free.  The
  final span lookup is index arithmetic and pointer loads.
- pcc's present hash generations and span descriptors are immortal, writers
  are serialized by the allocator lock, and all failure points occur before
  sixteen span keys become visible.

## Current source constraints

1. Granule keys are `address >> 12`; every 64 KiB slab owns sixteen entries.
2. One 32-byte immutable span descriptor is shared by all entries.
3. Readers must acquire-load every published pointer they depend on.
4. Registration must preflight duplicates and reserve all metadata before the
   first leaf span becomes visible.
5. Metadata pages are included exactly once in
   `pcc_allocator_metadata_mapped` and total mapped capacity.
6. Registration failure must leave the slab eligible for the existing raw
   carve fallback; no partial object-family mapping may remain.
7. The exact-set fallback remains responsible for foreign/large/raw/minor/
   zpage/type/forwarding cases.
8. The real-pthread gate currently observes hash capacity and exact grow-byte
   formulas through `pcc_allocator_granule_table`; those assertions must be
   migrated to the radix topology, not deleted or tricked with a fake header.

## Proposal No.1 `[CONFIRMED]`

Use an immortal four-level radix of 4096 pointer slots per level.  A 4 KiB key
is divided into four 12-bit indexes, covering addresses below 2^60; supported
allocator addresses outside that range fail registration and take the raw
fallback rather than producing an inexact positive.  Each node occupies one
exact 32 KiB mapping, so root and child nodes share one allocation/accounting
rule.

The writer, under the allocator lock, preflights all sixteen leaf slots, then
allocates every missing internal node and the immutable span descriptor.  It
release-publishes child pointers from the bottom up; empty internal nodes may
remain after a later failure but no leaf mapping is visible.  Once all failure
points pass, it release-stores the same span pointer into all sixteen exact
leaf slots.  Readers acquire-load root -> L2 -> L3 -> leaf -> span and perform
no hash, compare or probe loop.

The existing hash helpers remain temporarily callable as compatibility
oracles for focused tests, but the allocator registration and hot fused query
must not allocate or consult the hash table after activation.  Completion
updates telemetry and pthread expectations to exact radix-node/span-arena
bytes and proves negative sentinels throughout ordinary and node-allocation
publication windows.

## Rejection line

Any partial leaf publication, false positive/negative, moving-GC retirement
failure, loss of real-pthread overlap coverage, or metadata-accounting mismatch
denies the candidate.  After correctness, alternating heavy-object pairs must
show a majority favouring the candidate, lower span-lookup share, improving
wall/CPU/instructions and no material RSS regression.  Otherwise retain the
hash map and record `[DENIED]`; never weaken provenance to obtain the number.

## Test plan

1. Add focused source/runtime tests for address decomposition, same-leaf and
   cross-leaf sixteen-entry publication, duplicate rejection, high-address
   fail-closed behavior, and deterministic node-allocation failure before leaf
   publication.
2. Rewrite the pthread metadata oracle to count radix nodes plus span arenas;
   retain ordinary/grow overlap and permanent-negative sentinel requirements.
3. Run granule/provenance/layout, real-pthread grow/publication, GC3/GC4 moving
   fallback retirement and requested GC0..4.
4. Run the current heavy-object alternating A/B before any Stage2 rebuild.

## Result `[CONFIRMED]`

The four-level radix passed the complete 13-item granule/provenance/layout
gate and all 44 finalizer/weakref/resurrection/trashcan tests on each GC0..4.
The pthread metadata oracle now exactly includes radix nodes rather than
weakening its accounting assertion.

A same-binary selector A/B on the retained heavy-object workload produced
correct output in every arm, favoured radix in 9/10 pairs, and measured
1.03803x wall / 1.03661x CPU with instructions 0.97184x, cycles 0.96690x and
footprint 0.99771x.  Selector instrumentation was then removed.  A current
pure-radix profile puts the complete fused predicate at 10.21% and the
decomposed radix helper at 0.65%; hash/find-slot are absent from the hot query
stack.

## Status

`DONE_STRONG`.  Durable evidence:
`docs/goal/evidence/2026-08-26-granule-span-radix.md`.

## Update — exact 32 KiB nodes and parent-transfer retraction

Each node holds exactly 4096 eight-byte slots, so mapping 64 KiB was reduced to
32 KiB and the pthread oracle retained exact node-count accounting.  The
13-item correctness gate remains green.

The first parent/module98 transfer used a pcc1 whose Stage1 `ensure_runtime`
was only 1.288 s and therefore reused a differently optimized runtime archive.
A current `PCC_PYTHON_IR_PASSES=off` rebuild took 23.798 s; disassembly changed
the same objecthood function from a 276-byte register-resident body to a
436-byte stack body.  Current matched module98 is only about 1.05x and misses
the parent row's 1.10x bar.  Radix remains `DONE_STRONG` at its own same-binary
heavy-workload scope (1.038x); no Stage2 transfer claim survives.
