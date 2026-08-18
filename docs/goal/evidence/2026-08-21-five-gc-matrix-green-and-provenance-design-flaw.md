# Five-GC matrix GREEN + the provenance-index design flaw, profile-confirmed

Date: 2026-08-21

## 1. The five-GC bootstrap matrix passes, fixed point included

```
gc0            1 passed   920.79 s   (full stage1->2->3 + cmp pcc2 pcc3)
gc1..gc4       4 passed   900.77 s   (canonical xdist matrix invocation)
```

All five backends complete the three-stage self-host and the pcc2/pcc3
byte-identity check on the current tree. The previous run of this matrix
failed on ALL five backends at `cmp pcc2 pcc3` with a 100-byte
`__TEXT,__text` delta traced to a divergent mov/movk immediate-encoding
choice (docs/investigations/pcc2-pcc3-fixed-point-text-section-100-bytes.md).
A single-binary double-compile probe on the current tree is deterministic and
the cross-generation comparison now passes; the specific commit that fixed the
divergence was NOT isolated (recorded as fixed-unattributed).

## 2. Design flaw, found and profile-confirmed: per-object provenance hashing

Mechanism, verified in source:

```
every pcc_gc_alloc      graph_lock + hash-set INSERT of the object pointer
                        (py_obj.py: pcc_gc_pointer_register after every alloc)
every free              note_object_freeing does an INSERT (to "preserve exact
                        provenance") and then the REMOVE -- two hash writes
every provenance ask    graph_lock + open-addressing probe
                        (pointer_is_managed: barriers, class checks, dunder
                        dispatch, dict, format all route here)
```

pcc maintains a process-global open-addressing hash set of EVERY LIVE OBJECT
to answer "is this pointer a managed object?".  None of the five reference
collectors do this:

```
Go     spanOf(obj): two-level arenas array indexed by address bits
       (go-greentea/mgcmark.go:1702 and the heapArenas snapshot at :143)
ZGC    ZPageTable = ZGranuleMap<ZPage*>: flat array indexed by
       offset >> granule shift; get() is one shift + one load
       (zgc/zPageTable.hpp:43, zPageTable.inline.hpp:43)
```

Pages register once at mapping time; objects register never.  pcc's own
allocator already works in 64 KiB slabs (`page_alloc(65536)`,
freestanding_allocator.py:310), so the granule is ALREADY aligned with the
reference design.

Measured share (two independent profiles, leaf-attributed):

```
frozen real stage2 emit worker (16,032 samples, complete-v2.folded)
  index machinery (find_slot/insert/remove/contains/is_managed/register)
                                   21.26%
  graph locks (wrap register+probe) 10.32%
live current-tree pcc1, 600-fn compile (24,415 samples)
  index machinery                  18.98%
  graph locks                       5.26%
```

Callers spread across every subsystem (re_engine, stackmaps, arena, stackprep,
analysis, emitter) — a per-operation tax, exactly what a design flaw looks like
versus a hotspot.

Scope fact checked before proposing: the hash set is NOT the sweep
enumeration — the full-table walk at py_gc_index_table.c:608 is inside
`pcc_gc_managed_pointer_rehash`, and tracing/sweep iterate the separate
object-node lists.  The index is provenance-only (plus the same-file object
index used by forwarding), so a granule-map replacement does not have to
reinvent object enumeration.

## Proposed successor row (design-first)

Replace the per-object provenance set with a slab-granule map:

```
primary   granule map keyed on addr >> 16, one entry per 64 KiB slab /
          large mapping, registered at page_alloc/page_free time
          query = shift + flat-array load + tag compare (ZGC shape)
objecthood within an owned page: header check is SAFE (our memory) --
          distinguish PyObject pages / raw pages by slab class or header magic
auxiliary tiny explicit set kept ONLY for foreign registrations
          (capi shim objects, static/immortal globals) -- the existing
          ARCH-P1-MANAGED-POINTER-PROVENANCE decision already enumerates them
ceiling   19-21% of worker time plus part of the lock share and the
          per-alloc/per-free hash writes; also removes the >=2x-live-objects
          slots array from the footprint high-water mark
```

Five-GC equality, forwarding-index interaction (GC3/GC4), and the C+port
mirror rule make this a multi-slice structural row, not a session patch.

## 3. Same-knob stage trio: pcc2 reaches parity with CPython

All three numbers from one knob family (frontend_jobs=4, self_backend_jobs=2,
full work: gate stages ran cache-MISS, the stage1 arm ran cache-off; same
source, same machine, same day):

```
stage1  host CPython compiling pcc    351.0 s wall    643 s user
stage2  pcc1 compiling pcc            437.2 s wall  1,011 s user   1.25x of stage1
stage3  pcc2 compiling pcc            356.0 s wall    918 s user   1.014x of stage1
```

The headline: **pcc2 — the compiler pcc built of itself — compiles the whole
pcc source at parity with CPython running the same frontend (356.0 s vs
351.0 s)**, where this session started from a 9.89x frontend-only gap and
6-15x wall gaps. pcc1 (built by the host) is 1.25x. pcc1 and pcc2 are
different binaries by contract (the fixed point is pcc2==pcc3, which holds on
all five backends); stage3 also enjoys warmer caches than stage2 inside one
chain, so the 437-vs-356 split between them carries a warmth confound —
the parity claim is anchored on stage3-vs-stage1, both late-in-day warm-OS
runs doing full compilation work.

Honest remainder against the goal ("stage2 should be FASTER than stage1"):
pcc1 is 1.25x slower, pcc2 is at parity, neither is faster yet, and the user
CPU tells the truth about where the margin lives: pcc2 burns 918 s of CPU to
CPython's 643 s — a 1.43x per-instruction-work gap that parallelism currently
hides. The two registered structural rows own that margin: the provenance
granule map (19-21% measured ceiling) and the value lane.
