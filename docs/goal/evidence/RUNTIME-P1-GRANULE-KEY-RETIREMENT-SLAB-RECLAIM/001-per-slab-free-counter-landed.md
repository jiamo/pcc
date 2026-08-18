# RUNTIME-P1 Step 1: per-slab free counter + reclaimable gauge (LANDED)

## What landed

Empty-slab reclamation needs to know, per raw slab, how many cells are free.
Step 1 adds that tracking WITHOUT any reclamation behavior change, so it cannot
corrupt GC state, and it exposes the measurement that will verify the
reclaimability assumption before the risky trim (Step 2) is built.

Design (port-only, `pcc/py_runtime/py/freestanding_allocator.py`):

- The per-slab free-cell counter lives in the EXISTING span descriptor, not in
  slab memory.  The slab base has no free header: `first = slab+48` and
  `initialize_small` writes the first cell's negative-offset header at
  `slab+0..47`, so the whole 64 KiB slab is tiled by cells.  The span is a
  separate immortal record (one per slab, all 16 granules share it), so putting
  the counter there is collision-free.
- `_span_new`: span record grown 32 -> 40 bytes; offset 32 = free-cell count,
  initialized 0.  Offset 24 already caches the carve count (= total cells) for
  the class, so "fully free" is `span[32] == span[24]`.
- `refill_small`: after registering the kind-2 slab, set `span[32] = count-1`
  (the cells put on the free list; the returned first cell is live).
- `free()` (raw branch): reuses the ONE span lookup it already did to route
  object vs raw, increments `span[32]`, and when it reaches the total bumps a
  global `pcc_allocator_fully_free_slabs`.  Fallback slabs have no span and are
  simply untracked (guarded by `ptr_is_null(span)`), so the counter never
  touches the fallback path's cells.
- `malloc()` (take-small hit): decrements `span[32]`; if the slab was fully
  free, decrements the global.  This is the only added hot-path lookup; it is
  part of the reclamation feature (trim needs the counter), justified by the
  multi-GB RSS the eventual trim returns.
- New export `pcc_allocator_reclaimable_slab_bytes()` = fully-free slab count *
  65536: the upper bound a quiescent-point trim could return to the OS.

## Why the span-based design (not slab-tail metadata)

An earlier tail-metadata design (counters at slab+65472..) required reducing the
raw carve count and collided with the registration-failure fallback path (which
carves full object-count slabs then reuses them as raw).  The span-based design
needs neither a carve-count change nor fallback special-casing.

## Gates (all green)

- `tests/python/test_freestanding_allocator.py -k "reclaim or closed_platform_abi
  or c_abi or self_backend_uses_same"`: 4 passed in 5.57s.  New test
  `test_freestanding_allocator_tracks_reclaimable_empty_slabs` proves the
  counter's inc/dec balance and the gauge; the closed platform ABI stays
  EXACTLY {mmap, munmap} (no new syscall symbol); the C-ABI size/realloc matrix
  is unregressed.
- ARCH-P0 S1 gate `tests/python/test_gc_granule_map.py
  test_runtime_pointer_provenance.py test_runtime_layout_contract.py`:
  13 passed in 274.03s (includes GC0..4 runtime-archive rebuilds).  The span
  32->40 extension does NOT break the granule-map concurrency contract
  (real-pthread reader races, grow-preserves-keys, GC3/GC4 forwarded-source
  retirement) or pointer provenance under any of the five GC backends, or the
  runtime layout contract.

## Not yet done (Step 2, gated behind ARCH-P0)

The actual RSS reduction (empty-slab trim: unthread fully-free kind-2 slabs,
munmap them, retire their granule keys) is the delicate part that modifies the
IN-PROGRESS ARCH-P0 granule-map registration/rebind concurrency invariants
(key retirement needs open-addressing tombstones or relaxing the deliberate
never-rebind rule).  It also needs a quiescent-point trim call site and a pcc1
rebuild + capped Stage2 re-measure to prove the worker's 6.58 GiB high-water
falls under the 8 GiB cap.  See ../PERF-P0-STAGE-RESOURCE-ENVELOPE-PARITY/001
for the measured owner and the closed-ABI / kind-2-don't-care constraints.
