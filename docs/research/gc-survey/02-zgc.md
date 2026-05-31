# ZGC — colored-pointer concurrent moving GC for the JVM

**Source:** `docs/refs_docs/gc-research/zgc/` (OpenJDK `jdk-27+21`,
commit `4e1dd4daba5a619bbbc9720fdd509e609d6f0032`)

This survey now targets modern Generational ZGC. The exact reference pack is
recorded in `docs/refs_docs/gc-research/zgc/MANIFEST.json`; provenance and
scope are documented in `docs/refs_docs/gc-research/zgc/UPSTREAM.md`.

## What it is

ZGC (Z Garbage Collector) is OpenJDK's flagship low-latency collector, designed for **sub-millisecond pauses on multi-TB heaps**. It is:

- **Concurrent everything** — marking, relocation, reference processing all run in parallel with the application threads. STW pauses are ≤ 1 ms regardless of heap size.
- **Region-based** — heap divided into "ZPages" of 2 MB / 32 MB / N×32 MB.
- **Generational** in current OpenJDK. JDK 21 introduced Generational ZGC,
  JDK 23 made it the default, and JDK 24 removed the non-generational mode.
- **Moving / compacting** — relocates objects to defragment regions.
- **Colored pointers** — uses high bits of every reference to encode GC state. The machine pointer is "tagged" with phase/age info; load barriers strip the tag and remap.

The signature trick is the **load barrier**: every reference load goes through a fast-path test on the colored pointer's tag. If the color is "good" (meaning: matches the current GC phase), the load is a single instruction comparison. If the color is stale, the slow path remaps the object and updates the pointer in place ("self-healing").

## Heap layout

```
Heap address = | unused | metadata bits | virtual address |
                 high                              low
```

Pre-OpenJDK 18: 4 metadata bits ("colored pointers") via multi-mapping the heap at 4 different address ranges, all aliased to the same physical memory.

JDK 18+: switched to "load shift" using **load barrier** masks; doesn't need multi-mapping anymore.

ZPages are managed by `ZPageTable` — a flat lookup table from address → page metadata. Allocation is per-thread bump pointer in a TLAB (thread-local allocation buffer) carved from the current page.

## Hot paths

### Allocation (concurrent, lock-free)
```cpp
// zHeap.cpp::alloc_object — fast path is just bump pointer in TLAB
//   if exhausted, ask page allocator for a new page
oop ZHeap::alloc_object(size_t size) {
    return _object_allocator.alloc_object(size);
}
```

### Load barrier (the heart of ZGC)
```cpp
// zBarrier.hpp — every reference load goes through this
template <ZBarrierFastPath fast_path, ZBarrierSlowPath slow_path>
oop ZBarrier::barrier(volatile oop* p, oop o) {
    if (fast_path(o)) return o;       // 95%+ — single instruction
    return slow_path(p, o);            // remap + self-heal pointer
}
```

The fast path is one comparison + branch. Slow path is taken when the pointer's color doesn't match the current GC phase (e.g. mid-relocation): it walks the forwarding table, returns the new address, and writes it back to the original slot ("self-heal"). The next load of the same slot is fast again.

### Concurrent marking
- STW pause to install the load barrier color and snapshot roots (microseconds).
- Worker threads scan strong roots concurrently with mutator threads.
- Mutator-side load barrier ensures the SATB (snapshot-at-the-beginning) invariant: any reference loaded during mark gets pushed to the mark stack before the GC sees it.
- Final marking pause to drain handshake queues.

### Concurrent relocation
- Pages selected for compaction are added to the relocation set.
- Concurrent threads copy live objects out of relocation pages into fresh pages.
- The forwarding table maps old → new addresses.
- Mutator threads that load a stale reference get the new address from the slow path and self-heal.
- Once the relocation set is empty, old pages are reclaimed.

## Pause / latency profile

- **STW pauses: < 1 ms target**, typically 100-500 microseconds. Constant time regardless of heap size.
- **Concurrent overhead: 5-15%** allocation throughput hit due to load barrier.
- **Memory overhead: ~2x** (forwarding table + region metadata + needs headroom for relocation).
- **Generational ZGC** reduces the 5-15% overhead by collecting the young
  generation more frequently with cheaper barriers. For pcc #4 this means
  `zGeneration.*`, `zRemembered*`, and `zStoreBarrierBuffer.*` are part of
  the target design, not optional later reading.

## Mapping to pcc's 12 hooks

| pcc hook | ZGC equivalent | Note |
|---|---|---|
| `pcc_gc_alloc(size, type, flags)` | `ZHeap::alloc_object` — TLAB bump | Per-thread, lock-free |
| `pcc_gc_retain(o)` | **None** — ZGC has no refcount | ZGC pinning would go through different mechanism |
| `pcc_gc_release(o)` | **None** | Tracing collector — release is implicit |
| `pcc_gc_load_ptr(owner, slot)` | **THE load barrier** — `ZBarrier::barrier` | Most important hook for ZGC |
| `pcc_gc_store_ptr(owner, slot, val)` | Plain store + occasional SATB barrier | "card mark" equivalent on remembered set |
| `pcc_gc_store_root(slot, val)` | Same as store_ptr | Roots are scanned via thread handshake |
| `pcc_gc_frame_enter(frame_map, slots)` | Stack frame info registered with stack walker | ZGC needs precise stack maps |
| `pcc_gc_frame_leave(slots)` | unregister | |
| `pcc_gc_safepoint()` | Polling check — handshake target | Concurrent GC needs frequent safepoints |
| `pcc_gc_collect(reason)` | `ZDriver::run` — kicks the cycle | Async — returns before GC finishes |
| `pcc_gc_pin(o)` | Mark page as not-relocate-this-cycle | For FFI safety |
| `pcc_gc_unpin(o)` | Lift the pin | |

**Verdict:** ZGC's design **requires** read barriers and frame maps that pcc doesn't yet emit. The 12 hooks codex landed are well-shaped for this — but several are no-ops today and would need codegen work to actually emit calls.

## Porting to pcc as backend #4

Modern GenZGC is the **most ambitious** backend to consider, because it
requires:

1. **Read barrier in IR** — every `pcc_gc_load_ptr` must compile to a real fast-path comparison. Today it's just a load. Codegen has to emit:
   ```llvm
   %raw = load ptr, ptr %slot
   %tag_ok = call i1 @pcc_gc_color_ok(ptr %raw)
   br i1 %tag_ok, label %fast, label %slow
   ```
   Estimated: extending layer1.py — **2-3 weeks** because every dereference site needs this.

2. **Forwarding table** — runtime data structure mapping old → new addresses during relocation. Use `zForwarding*`, `zRelocate.*`, and `zRelocationSet*` as the upstream shape.

3. **Stack maps** — frame_enter/leave hooks must register precise pointer slot info. self-backend needs to know which SSA values in each frame are pointers vs ints. Doable but invasive: **3-4 weeks**.

4. **Page allocator** — can't bump-allocate without ZPages. Use
   `zPage*`, `zPageAllocator.*`, and `zPageTable.*` as the upstream shape.

5. **Concurrent worker threads** — pcc is single-threaded today. ZGC's whole point is concurrency. Requires `pthread` or equivalent. **2 weeks** for the GC worker; **months** if pcc multi-threading isn't already in.

6. **Self-heal store** — the slow-path load barrier writes back to the slot. Requires the slot reference, not just the value. Codegen has to pass `&slot` in `pcc_gc_load_ptr` (which the hook signature already supports).

7. **Young/old and remembered sets** — current OpenJDK ZGC is generational.
   Backend #4 cannot claim modern ZGC parity without store barriers,
   remembered sets, young/old policy, and relocation rules that cooperate
   with those generations.

**Total estimate:** 3-6 months solo, gated on pcc multi-threading first.

**Realistic positioning:** ZGC is **not the next backend pcc should write**. It's the backend pcc could grow into **after** generational + concurrent infrastructure exists. Listed here as the "where could this go" reference design — not as a near-term task.

## What pcc would learn from porting ZGC

- **Pin/unpin already in the hook set** — codex anticipated this. Good.
- **Load barrier is the killer feature, not the cost.** Once present, pcc could use it for read-side optimizations beyond GC (e.g. lazy loading, copy-on-write across address spaces, NUMA-aware migration).
- **Generational ZGC (JDK 21+) is the practical version.** Single-gen ZGC has 5-15% overhead; gen-ZGC is closer to G1's overhead with similar pauses. If pcc ever does ZGC-like, it should be generational from day one.
