# Design + pre-registration: slab-granule provenance map

Date: 2026-08-21
Row: `ARCH-P0-PROVENANCE-GRANULE-MAP` (design-stage exit artifact)
Status: DESIGN HISTORY + PRE-REGISTRATION. S1/v2 source was subsequently
implemented, but current acceptance remains open; see
`2026-08-21-granule-map-v2-correctness-gates-and-stage-proof.md` for the
corrected current claim boundary.

## The measured flaw (recap, both profiles leaf-attributed)

```
frozen real stage2 emit worker    index machinery 21.26%, graph locks 10.32%
live current-tree 600-fn compile  index machinery 18.98%, graph locks  5.26%
per pcc_gc_alloc                  locked hash INSERT of the object pointer
per free                          INSERT + REMOVE (two hash writes)
per provenance question           locked open-addressing probe
```

The user-CPU gap this owns: pcc2 burns 918 s CPU where CPython burns 643 s on
the identical task (1.43x), while wall is at parity only through parallelism.

## Source-verified facts the design must respect

1. **Slabs are mixed.** The freestanding allocator exports `malloc`/`free`/
   `calloc` themselves (freestanding_allocator.py:362/381/412), so raw runtime
   allocations and Python objects share the same 64 KiB slabs today. A
   granule-hit-then-header-read scheme is therefore UNSOUND: raw bytes can
   counterfeit a header, and provenance here must be exact.
2. **Objecthood must come from structure, not content.** The sound shape is
   Go's mspan: object allocations move to OBJECT-ONLY slabs; a granule entry
   records {kind: object-slab(classK) | raw-slab | large-object(base,len) |
   large-raw | foreign-none}; "is managed object pointer" =
   granule hit ∧ kind=object ∧ (offset − first_slot) % class_stride == 0.
   Exact, O(1), no header read, no per-object state.
3. **Granularity is already aligned.** Slabs are `page_alloc(65536)`
   (freestanding_allocator.py:310); large allocations retain their own
   mapping descriptor. ZGC's `ZPageTable = ZGranuleMap<ZPage*>`
   (zPageTable.hpp:43, .inline.hpp:43 — one shift + one load) and Go's
   `spanOf`/heapArenas (go-greentea/mgcmark.go:143,1702) are the in-repo
   references.
4. **Sparse addresses need two levels or a granule hash.** Slabs come from
   mmap anywhere in a 47-bit space, so a flat array is out. v1: keep the
   existing open-addressing machinery but key it on `addr >> 16` — inserts
   drop from per-object (millions) to per-slab (thousands, once per slab
   lifetime), the table is thousands of entries and cache-hot. v2 may move to
   a two-level radix if the probe still shows.
5. **The per-object set survives only for foreigners.** The capi shim and
   static/immortal registrations (the explicit `pcc_gc_pointer_register`
   call sites in py_capi_shim*.c, py_class.c statics) keep a small exact set;
   allocator-owned objects never touch it. Call-site inventory to convert:
   32 `pcc_gc_pointer_is_managed` askers, 16 `pcc_gc_pointer_register` sites.
6. **Forwarding is out of scope.** GC3/GC4 use the separate object index for
   forwarding; the sweep walks object-node lists; the only full-table walk of
   the provenance set is its own rehash (py_gc_index_table.c:608). Replacing
   provenance does not touch enumeration or forwarding.
7. **Mirrors.** C authority: py_gc_index_table.c + allocator C mirror; port:
   freestanding_gc_index_table.py + freestanding_allocator.py. Both change in
   the same slice with the differential-equality rule; the allocator gains an
   object/raw family split of its 8 size classes (take/put/refill duplicate
   per family or gain a family flag).

## Slices, each with its own gates

```
S1  allocator family split (object vs raw slabs) + granule registration at
    slab birth/death; provenance still answered by the old set (no behavior
    change); gates: allocator/layout tests + five-GC smoke + bootstrap baseline
S2  pointer_is_managed/is_managed_no_lock re-routed to granule+stride;
    per-object register/unregister for allocator objects becomes a no-op;
    the free path drops its insert+remove pair; foreign set stays;
    gates: tests/python/test_runtime_pointer_provenance.py,
    test_runtime_layout_contract.py, GC0..4 focused suites, bootstrap baseline
S3  frozen module98 worker A/B, pre-registered bar >=1.10x paired median wall
    (measured ceiling ~20% + lock share), user+sys and instructions improving,
    RSS <=1.00x (the >=2x-live-objects slots array disappears), assembly
    byte-identical, every produced binary RUN with outputs compared
    (the compile-rc-only harness produced a false 5.9x once; never again)
S4  complete stage2 + the five-GC matrix, only after S1-S3 green
```

Rejection lines: S2 red on any provenance/layout/GC gate = stop, no tuning;
S3 first pair below 1.03x may stop and DENY the remainder (the fixed
per-question win is architectural, but honesty over hope); any stage red in
S4 reverts the slice per the bootstrap regression discipline.

## Explicit non-goals

Barrier semantics, finalizers, forwarding tables, object enumeration, the
foreign-object registration contract, and any allocator policy change beyond
the family split. Bundling any of these voids the pre-registration.

## S1 landed and green (2026-08-21)

What went in, with the one architectural correction the tree itself forced:

```
granule map        allocator-OWNED: the first placement (GC index-table layer)
                   was rejected by the freestanding layering verifier, and
                   rightly -- its rehash allocated through calloc, which
                   re-enters the allocator under the allocator's own lock.
                   The map now lives in freestanding_allocator.py with tables
                   from page_alloc (grow = map new block, reinsert, free old),
                   registration naturally inside the already-held lock.
object slab family 11 size classes duplicated (obj free-list heads),
                   take/put/refill_small_object; refill registers the slab's
                   granule(s): kind 1, and kind 6 for the spill granule when a
                   16 KiB-aligned 64 KiB slab crosses a 64 KiB boundary;
                   payload = (stride << 16) | (slab_base & 0xFFFF)
routing            pcc_gc_alloc's fallback now draws from the object family
                   (pcc_allocator_alloc_object); free() family-routes small
                   cells by granule kind; large objects unchanged (raw path +
                   per-object registration, as designed)
provenance answers UNCHANGED in S1: the per-object set stays authoritative;
                   the granule data merely accumulates truth
C side             honest cc-mode stubs (libc malloc has no slabs; lookup
                   always misses; every caller falls through to the old set)
verified           closure checks on both modules; archive rc=0; stage1 rc=0;
                   hc/MIN/rooty/cr_100 correct; granule+provenance gates
                   5 passed on backends 0, 3 and 4; bootstrap baseline green
regression         tests/python/test_gc_granule_map.py pins register/lookup/
                   rehash-survival/deletion-compaction through a compiled probe
```

S2 next: reroute `pointer_is_managed` to granule+stride with old-set fallback
(foreign/large/pool objects), and stop per-object registration for family
objects — that is where the measured 19-21% begins converting.

## Update 2026-08-21: v2 rewrite after review (P0-1/P0-2/P0-3, P1-4/P1-5 + Linux P0)

The S1 granule map was reviewed and found unsound; v2 landed the same day in
`pcc/py_runtime/py/freestanding_allocator.py`:

- **4 KiB OS-page granules** (`addr >> 12`, sixteen per 64 KiB slab). 4 KiB is
  the smallest `page_alloc` alignment across platforms (Linux guarantees only
  4 KiB; macOS gives 16 KiB), so one granule always belongs to exactly one
  mapping and the 64 KiB-key neighbor-clobber (P0-1) and the Linux
  misalignment P0 are both impossible by construction. `register_slab`
  additionally refuses non-4 KiB-aligned bases.
- **One stable span descriptor per slab**: a 32-byte immortal record
  `{kind, stride, base, 0}` bump-allocated from never-freed `page_alloc`
  chunks; all sixteen granules point at the same descriptor, so readers can
  never see torn metadata and `is_object_start` recomputes nothing.
- **Immutable snapshot + release/acquire publish (P0-2)**: the key table is
  one block `[cap | keys[cap] | spans[cap]]` behind a single global pointer.
  Growth builds the new block privately and publishes it with a release
  store; readers load the pointer with acquire; old snapshots are leaked on
  purpose (bounded by ~2x the final table). Inside `bind`, the span slot is
  written BEFORE the key, and the key is published with a release store;
  `find_slot` loads keys with acquire — a lock-free reader that observes a
  key therefore always observes its span. `unbind` (in-place cluster
  compaction) is documented test/rollback-only.
- **Transactional registration (P1-4)**: all sixteen keys bind or the bound
  prefix rolls back. On failure the object refill does NOT allocate another
  slab (that leaked the accounted mapping): it reuses the same slab as a raw
  carve, which matches the free() route on granule miss.
- **Stride validation (P0-3)**: kind-1 registration and the objecthood query
  both validate the stride against the 11-class carve table before any
  modulo; `is_object_start` only answers a fast POSITIVE (raw slabs can hold
  C-tier self-registered objects, so negatives fall back to the exact set).
- **ABI unified (P1-5)**: header + cc-mode stubs now declare
  `pcc_gc_granule_register_slab/span/kind/is_object_start`; the `_abi` name
  and the v1 register/lookup/unregister surface are gone.

Historical focused runs reported object/raw/live/free/interior behavior,
600-slab growth and allocator/provenance/layout checks passing. The associated
“real-pthread” claim is **withdrawn**: the tested pcc-Python `Thread.start()`
path ran each target synchronously, and setting `PCC_WITH_THREADS=1` around
compilation did not itself prove selection of a pthread runtime archive. That
case is serial evidence only; it does not prove concurrent snapshot growth,
rollback/rebind publication, or the supported single-writer-under-allocator-
lock protocol.

## Superseded receipt note

The former “Current boundary” paragraph in this design-history document is
historical and must not be used as current acceptance evidence. In particular,
its `3 passed in 282.43s` receipt used a broad `writer_active` interval and did
not prove that a complete lookup overlapped one particular ordinary or table-
grow registration call. Its exact-provenance/layout and GC3/4 moving-runtime
receipts also predate the current allocator hash. The old statement that
granule metadata was omitted from mapped-capacity telemetry is now false:
current source accounts every table generation and span arena in total mapped
capacity while keeping requested/usable payload counters unchanged.

The durable current facts, source hashes, corrected per-registration odd/even
pthread receipt, negative sentinel coverage, duplicate-at-candidate-16 gate,
allocator-family/large-object coverage, stale-receipt boundaries and separate
exact-index allocation-rollback task are maintained only in
`2026-08-21-granule-map-v2-correctness-gates-and-stage-proof.md`. The exact
managed-pointer set remains production provenance authority and the S2 helper
still has zero production callers, but this design artifact makes no current
S1/S2, stage, performance, fixed-point or five-GC acceptance claim.
