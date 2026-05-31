# OCaml 5 GC — multicore generational concurrent

**Source:** `/tmp/gc-research/ocaml/` (OCaml 5.2)
- `major_gc.c` (74 KB) — major (old gen) collector
- `minor_gc.c` (32 KB) — minor (young gen) collector
- `shared_heap.c` (47 KB) — multi-domain shared heap
- `gc.h` (2 KB) + `major_gc.h` (3 KB) — public interface

## What it is

OCaml 5 (released 2022) brought multicore parallelism to OCaml. The GC was redesigned to support multiple "domains" (OS threads with concurrent OCaml execution):

- **Generational**: minor (young) heap per domain + shared major heap.
- **Minor GC**: per-domain stop-the-world copying collector. Sub-millisecond. Promoted survivors go to the major heap.
- **Major GC**: shared, **incremental**, and **mostly concurrent** (Yuasa snapshot-at-the-beginning + write barrier).
- **Compacting**: minor GC compacts young; major GC compacts during certain phases. Some pointer-stability issues handled via "ephemerons".
- **Heap layout**: pool-based for small allocations, large objects get their own pool entry.

OCaml 5's GC publication ("OCaml at Scale", 2022) emphasizes:
- Domain-local minor heaps for low-overhead young GC
- Mostly-concurrent major GC with bounded pauses
- Type-precise marking (compiler tells GC exactly which fields are pointers)

## Heap layout

Every OCaml block has a header word:

```
+----------------+----------------+
| size (54 bits) | tag (8 bits)   |
+----------------+----------------+
| color (2 bits) | reserved       |
+----------------+----------------+
```

The color bits ride along in unused header space. Three colors: marked, unmarked, garbage (`shared_heap.c::caml_global_heap_state`).

The shared heap is divided into **size-class pools** with intrusive free lists, similar to Go but cross-domain.

## Hot paths

### Minor allocation (per-domain, lock-free)
```c
// fast path: bump pointer in domain's minor heap
//   if hits limit, trigger minor GC for this domain
caml_alloc_small(size, tag) → bump_ptr += size; if (bump_ptr > limit) trigger_minor_gc()
```

### Minor GC (Cheney-style copying)
1. STW the calling domain (other domains keep running on their own minor heaps).
2. Scan domain-local roots (registers, stack).
3. Walk the remembered set: any major→minor pointers from old objects.
4. Copy live young objects into the shared major heap.
5. Reset the minor heap pointer to start.

Pause time: ~100 µs per domain, scales with **young-survivor size**, not heap size.

### Major GC (concurrent + incremental)
The main driver in `major_gc.c`. Phases:

1. **Mark** — Yuasa snapshot-at-beginning. Concurrent worker thread (or sliced into mutator time slices) drains a per-domain mark stack.
2. **Sweep** — concurrent free of unmarked objects in pools.
3. **Compact** — periodic, full STW. Object addresses stable between compactions; ephemerons handle the corner case.

The mark stack is split into "stack" (precise spans) and "compressed stack" (bitmap). Allows bounded memory for the mark queue.

```c
// major_gc.c
#define MARK_STACK_INIT_SIZE (1 << 12)
/* The mark stack consists of two parts:
   1. the stack - a dynamic array of spans of fields that need to be marked, and
   2. the compressed stack - a bitset of fields that need to be marked. */
```

### Write barrier (Yuasa SATB + remembered set)
Two purposes per write:
- **Major write barrier**: if old object gets new pointer, snapshot the old pointer onto mark stack.
- **Minor write barrier**: if major→minor pointer created, add slot to remembered set so next minor GC scans it.

These are inserted by the OCaml compiler (`caml_modify` macro or its inlined form).

### Multidomain coordination
- Domains take a global handshake to enter "major slice" or compaction.
- Major GC progress is per-domain; the slice is interruptible.
- Backed by domain barriers + atomic mark color flips.

## Pause / latency profile

- **Minor GC: ~100 µs per domain** — depends on young survivor count, NOT heap size. Dominates production GC pause budget.
- **Major GC mark/sweep: < 1 ms slices** — mostly interleaved with mutator.
- **Compaction pauses: 10-100 ms** — rare, only fires on heavy fragmentation.
- **Throughput cost: 5-15%** for the write barrier + concurrent worker.

## Mapping to pcc's 12 hooks

| pcc hook | OCaml 5 equivalent |
|---|---|
| `pcc_gc_alloc(size, type, flags)` | `caml_alloc_small` (minor) / `caml_alloc_shr` (large/major) |
| `pcc_gc_retain(o)` | **None** — pure tracing |
| `pcc_gc_release(o)` | **None** |
| `pcc_gc_load_ptr(owner, slot)` | Plain load — no read barrier |
| `pcc_gc_store_ptr(owner, slot, val)` | `caml_modify` — full write barrier (SATB + remembered set) |
| `pcc_gc_store_root(slot, val)` | Plain store; roots scanned at safepoint |
| `pcc_gc_frame_enter(frame_map, slots)` | OCaml uses precise stack maps emitted by the compiler — frame_enter would register pointer-slot bitmap |
| `pcc_gc_frame_leave(slots)` | unregister |
| `pcc_gc_safepoint()` | Function epilogues + back-edges; checks for major-slice request |
| `pcc_gc_collect(reason)` | `caml_gc_full_major` — synchronous full cycle |
| `pcc_gc_pin(o)` | "Custom blocks" can be marked uncopiable; or use ephemerons |
| `pcc_gc_unpin(o)` | inverse |

**Verdict:** OCaml's GC is the **most sophisticated** of the five and most production-tested for **multicore + low pause**. But it requires:
- Generational heap (young + shared old)
- Compiler-emitted stack maps for precise pointer info
- Multi-domain coordination

## Porting to pcc as G6.5 backend

OCaml-style GC is **the most ambitious functional fit** for what pcc could grow into:

| Component | Estimate | Notes |
|---|---|---|
| Per-domain minor heap | 2 weeks | bump-pointer + threshold trigger |
| Cheney-style minor copy | 2 weeks | with promotion to major heap |
| Pool-based major heap | 2 weeks | size-class pools, similar to Go |
| Mark stack with span+compressed-bitmap | 1 week | bounded memory |
| Yuasa SATB write barrier | 1 week | codegen change at every store |
| Remembered set (major→minor) | 1 week | card table or per-write log |
| Multi-domain coordination | 4 weeks | requires pcc multi-threading first |
| Ephemerons | 2 weeks | weak key-value pairs |
| Stack maps emitted by self-backend | 3 weeks | precise per-frame pointer info |
| Concurrent compaction | 4 weeks | optional; adds moving GC complexity |

**Total estimate:** 14-22 weeks for "OCaml 5 style" backend.

**Pre-requisites:**
- pcc multi-threading + pthread atomics
- Stack maps emitted by codegen (separate `metadata` section per function)
- Generational layout (minor + major separation)

## Why pcc would learn from OCaml's design

- **`pcc_gc_frame_enter(frame_map, slots)` already has the right signature** — codex foresaw compiler-emitted stack maps. OCaml is the canonical example of how this works in production.
- **Mark stack with compressed bitmap is novel** — bounds mark memory regardless of heap size. Useful for embedded targets.
- **Domain-local minor heap is the right scaling story** — when pcc grows multi-threading, you want per-thread alloc + per-thread minor GC, not a global lock.
- **Ephemerons solve weakref + cycle interaction** — Python's `WeakValueDictionary` is exactly the case OCaml ephemerons handle directly.

## Why pcc might NOT prefer OCaml's design

- **Compaction breaks `id(obj)` stability** — Python language guarantees stable id; OCaml has no such guarantee, so it's free to compact. pcc would need to disable compaction or switch to id-indirection.
- **Multi-domain is overkill** for single-threaded pcc workloads (compile-time tools, scripts).
- **Type-precise marking** requires the compiler to emit pointer bitmaps per object layout. pcc's `PyObjectHeader.type_tag` is coarser; would need extension.

## Quote from `major_gc.c` worth pondering

> "The mark stack consists of two parts: the stack — a dynamic array of spans of fields that need to be marked, and the compressed stack — a bitset of fields that need to be marked. The stack is bounded relative to the heap size."

The bitmap fallback is what makes OCaml's mark stack survive worst-case heaps (deeply pointer-dense graphs). Naive mark stacks can grow O(heap size); OCaml's caps it at O(heap pages). pcc's G1 cycle collector should consider this trick.
