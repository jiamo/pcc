# RUNTIME-P1 Step 2: empty raw-slab trim + granule key retirement (LANDED, host-validated)

## What landed (port-only, `pcc/py_runtime/py/freestanding_allocator.py`)

The actual RSS-reduction mechanism.  Builds on Step 1's per-slab free counter
(span offset 32) and returns fully-free RAW (kind-2) slabs to the OS.

- `_granule_find_slot`: tombstone-aware.  `-1` (never a valid granule key) is
  a tombstone left by retirement: the probe continues past it (chain intact)
  and a not-found result hands back the FIRST tombstone slot for reuse.
- `_granule_bind_new_locked`: reusing a tombstone slot does not raise the
  load-factor count (occupancy unchanged); only a fresh empty slot does.
- `_granule_grow`: the rehash purges tombstones and resets
  `pcc_allocator_granule_count` to the live re-inserted keys.
  Termination invariant: occupancy (live + tombstone) == count at all times;
  retire converts live->tombstone (occupancy unchanged), so `reserve`'s 50%
  load bound guarantees an empty slot and `find_slot` always terminates.
- `_granule_retire_slab_locked(slab)`: ONLY kind-2 spans (refuses otherwise).
  Zeroes the 16 radix leaf slots FIRST (release), then tombstones the 16
  flat-table keys.  Reader safety: no reader dereferences a raw slab's memory
  (every kind-1 consumer bails at kind != 1; `pcc_free` routes on kind), so a
  lock-free reader racing retirement sees either the immortal kind-2 span or
  null -- never an unmapped page.
- `_trim_rebuild_list_locked(head)`: rebuilds one raw free list dropping the
  cells of fully-free slabs; queues each such slab once (span[32] := -1 as
  the queued marker; later cells of that slab match the marker and drop).
  The queue links through slab+0 -- the dead first cell's HEADER, never a
  live free-list link (links live at user offset 0 = slab+48+i*stride).
- `_trim_locked()` / export `pcc_allocator_trim()`: rebuild all 11 raw lists,
  then for each queued slab: retire granules, `page_free` (munmap), account
  `pcc_allocator_mapped -= 65536` and `fully_free_slabs -= 1`.  A slab whose
  retirement fails (cannot happen for a slab queued from its own kind-2 span)
  is left MAPPED rather than unmapped under a live granule: a 64 KiB leak is
  preferable to a dangling span (fail closed).
- `pcc_allocator_refill_small`: before mmapping a NEW raw slab, if >= 4 fully
  free slabs (256 KiB) are idle, `_trim_locked()` first.  The retained
  footprint is therefore self-limiting across allocation phases: the
  allocator never holds fully-free slabs while also growing.
  `# ponytail: fixed 4-slab threshold; tune if refill thrash appears`.

Closed platform ABI unchanged: only the existing `page_alloc`/`page_free`
(mmap/munmap) are used; no madvise, no new syscall symbol.

## Why the slab base header is not the collision it looks like

`_granule_object_slot` / kind-1 readers require `slab_offset >= 48`, so they
never read slab+0..47.  For a kind-2 slab being reclaimed, every cell is free
(dropped from the list, no live user), so repurposing slab+0 as the trim
queue link during trim is safe; the slab is munmapped immediately after.

## Gates (all green)

- `tests/python/test_freestanding_allocator.py -k "reclaim or closed_platform_abi
  or c_abi or self_backend_uses_same or threaded_churn"`: 5 passed in 12.33s.
  `test_freestanding_allocator_tracks_reclaimable_empty_slabs` (two-phase):
  Phase 1 -- gauge 0 while live; >=1 slab after free-all; take/free
  dec/inc balance; explicit `pcc_allocator_trim()` returns >=1, mapped bytes
  fall by >=64 KiB, gauge -> 0; a following malloc (fresh slab possibly on the
  just-unmapped address) re-registers cleanly (tombstone reuse).  Phase 2 --
  five fully-free 16-class slabs idle, then a 1024-class refill auto-trims
  them before mmapping (gauge -> 0, net mapped falls).  Threaded churn (4
  pthreads, llvm + self backend) unregressed; closed ABI stays EXACTLY
  {mmap, munmap}; C-ABI size/realloc matrix unregressed.
- ARCH-P0 S1 gate `tests/python/test_gc_granule_map.py
  test_runtime_pointer_provenance.py test_runtime_layout_contract.py`:
  13 passed in 279.40s (GC0..4 archive rebuilds).  The tombstone changes to
  find_slot/bind/grow and the retirement path keep green:
  grow_preserves_all_sixteen_keys, single_writer_races_real_pthread_readers
  _through_grow, object_lifecycle_races_real_pthread_readers, live
  publication header exposure, GC3/GC4 forwarded-source retirement, C and
  pcc-Python pointer provenance under GC0..4, and the layout contract.

## What this does and does not prove

Proves: the reclamation mechanism is functionally correct, reader-safe under
real-pthread races, granule-map-invariant-preserving, and ABI-closed --
host-side, on the freestanding allocator compiled by pcc.

Does NOT yet prove: that the pcc1 codegen worker's measured 6.58 GiB
high-water (PERF-P0 evidence 001, pid 7143) falls under the 8 GiB cap.  That
needs a FRESH Stage1 (a pcc1 whose runtime includes this allocator -- the
existing inline-edge-stage1-capped-v2 pcc1 predates it and would prove
nothing) followed by the capped Stage2 from that receipt.  It also depends on
the temporal profile: trim lowers the high-water only where phases free one
class before growing another; a genuinely live peak working set is a
different (upstream codegen liveness) owner.
