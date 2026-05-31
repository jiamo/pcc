# Go Green Tea GC — span-local concurrent mark-sweep

**Source:** `/tmp/gc-research/go-greentea/` (Go master branch)
- `mgc.go` (78 KB) — main GC algorithm
- `mgcmark.go` (56 KB) — concurrent marking
- `mgcsweep.go` (33 KB) — concurrent sweeping
- `mgcscavenge.go` (53 KB) — memory scavenging back to OS
- `mwbbuf.go` (8 KB) — write barrier buffer (Yuasa-style snapshot-at-beginning)
- `HACKING.md` (22 KB) — design notes

## What it is

The current Go GC (Go 1.5+, evolved continuously) is described in `mgc.go` as:

> "concurrent mark and sweep that uses a write barrier. It is non-generational and non-compacting. Allocation is done using size segregated per P allocation areas to minimize fragmentation while eliminating locks in the common case."

**"Green Tea" GC** is the proposed evolution (RFC stage in 2026). The key change: shift from **per-pointer marking** (current Go) to **per-span marking** — when an object is reached, mark its entire span as gray. Workers then process spans rather than individual objects, getting much better cache locality on dense pointer graphs (linked lists, trees, GC-heavy pipelines).

Until Green Tea lands as default, the Go runtime is the classic "Go GC": tricolor + write barrier + concurrent mark + concurrent sweep + non-moving + non-generational.

## Heap layout

- **Heap is divided into "spans"** — each span is N contiguous 8 KB pages serving objects of one fixed size class (~70 size classes).
- **Per-P allocation cache** — each goroutine scheduler P has a `mcache` with a few free spans for fast lock-free alloc.
- **Central `mheap`** — locks rarely (when mcache empty).
- **Object headers are minimal** — pointer-bitmap is in spans, not in objects. Saves header overhead but requires precise size-class lookups.

```go
type mspan struct {
    next       *mspan
    prev       *mspan
    startAddr  uintptr
    npages     uintptr
    freeindex  uintptr
    nelems     uintptr
    allocBits  *gcBits     // bitmap: which slots are allocated
    gcmarkBits *gcBits     // bitmap: which slots are marked this cycle
    sweepgen   uint32
    // ...
}
```

## Hot paths

### Allocation (per-P, lock-free)
```go
// mgc.go::mallocgc → mcache.next_free → bump in pre-allocated span
// If span exhausted, ask mcentral for a new one
// If mcentral exhausted, ask mheap
// All lock-free in the fast path; locks only on cache miss
```

### Write barrier (Dijkstra-style + Yuasa hybrid)
Every pointer write goes through a write barrier when GC is active:
```go
// mwbbuf.go — write barrier buffer (per-P)
//   Records both old and new pointer values into a per-P queue
//   Periodically flushed to the global mark queue
```

The buffer is the key efficiency trick: instead of marking work synchronously per write, writes drop entries into a thread-local ring buffer. Flush happens at safepoints or on buffer fill. This batches mark work and amortizes synchronization.

### Concurrent marking (tricolor)
- **STW pause to install write barrier** (~50 µs).
- **Concurrent root scan**: stacks, globals, finalizable objects.
- **Worker goroutines drain mark queues**: pop pointer, mark target object, queue children.
- **STW pause to flush barrier buffers + mark termination** (~100 µs).
- All time critical sections are bounded; pause times: 100-500 µs typical, < 10 ms p99.

### Concurrent sweeping
- After mark, sweep is **lazy**: each goroutine, on alloc, sweeps one span before pulling from mcentral.
- This amortizes sweep cost over allocation, no global STW.

### Green Tea evolution
The Green Tea proposal moves from "object as work unit" to "span as work unit":
- Mark queue holds spans, not pointers.
- When pulling work, scan all live objects in the span at once.
- **Cache-friendly**: span is contiguous; one cache prefetch covers many objects.
- **Lower contention**: fewer queue pushes/pops on dense graphs.
- Expected: 10-30% mark-phase throughput improvement on pointer-heavy workloads.

## Pause / latency profile

- **STW pause target: < 1 ms** (achieved in production for normal workloads).
- **Throughput overhead: 25%** budget by default (controllable via `GOGC`).
- **Memory overhead: 2x heap** when GC is between cycles — this is the GOGC=100 default (collect when heap doubles).
- **No moving / no compaction** — `unsafe.Pointer` and address-stable interop with C are sound.

## Mapping to pcc's 12 hooks

| pcc hook | Go equivalent |
|---|---|
| `pcc_gc_alloc(size, type, flags)` | `mallocgc` — per-P bump + size-class lookup |
| `pcc_gc_retain(o)` | **None** — tracing GC, no refcount |
| `pcc_gc_release(o)` | **None** |
| `pcc_gc_load_ptr(owner, slot)` | Plain load — no read barrier (non-moving) |
| `pcc_gc_store_ptr(owner, slot, val)` | `*slot = val; gcWriteBarrier(slot, val)` — pushes (slot, val) into per-P buffer |
| `pcc_gc_store_root(slot, val)` | Same as store_ptr; roots are scanned via stack maps |
| `pcc_gc_frame_enter(frame_map, slots)` | Frame metadata registered via Go's stack maps; checked at safepoints |
| `pcc_gc_frame_leave(slots)` | implicit on function return |
| `pcc_gc_safepoint()` | `runtime.Gosched()` triggers; per-P preemption check |
| `pcc_gc_collect(reason)` | `runtime.GC()` — synchronous full cycle |
| `pcc_gc_pin(o)` | **Not needed** — non-moving GC means pointers stable |
| `pcc_gc_unpin(o)` | **Not needed** |

**Verdict:** Go's GC fits pcc's hooks **better than ZGC** because it's non-moving (no read barrier needed, no pin/unpin needed). The major gap: `pcc_gc_store_ptr` would need a **real write-barrier buffer** instead of pcc's current refcount sequence.

## Porting to pcc as G6.5 backend

Roughly mid-difficulty:

| Component | Estimate | Notes |
|---|---|---|
| Span allocator (size-segregated) | 2 weeks | Replaces per-object malloc; foundation |
| Per-P allocation cache | 1 week | "P" in pcc = compile thread or runtime worker |
| Pointer bitmap per span | 1 week | Identifies which slots are pointers |
| Tricolor mark stack | 1 week | Worker pops span, scans, marks children |
| Write barrier buffer | 2 weeks | Per-thread ring buffer; flush at safepoint |
| Concurrent marker thread | 2 weeks | Needs pcc multi-threading first |
| Lazy sweep on alloc | 1 week | Amortize sweep over allocation |
| Stack maps | 2 weeks | Self-backend needs to emit precise pointer info per frame |
| Green Tea span-marking | extra 2 weeks | Optional — built on top of base Go GC |

**Total estimate:** 12-14 weeks for "classic Go GC" backend, +2 for Green Tea on top.

**Gating:** pcc multi-threading. Without concurrent mark workers, this collapses to "stop-the-world tricolor", losing Go's signature low-pause property.

## Why pcc would consider Go-style GC

- **No read barrier** — codegen change is much smaller than ZGC. Just the write barrier needs new instruction sequence.
- **Non-moving** — pcc's `id(obj)` stability and FFI compatibility preserved without pin/unpin machinery.
- **Battle-tested** — Go production workloads of every size have been running this for a decade.
- **Tunable** — `GOGC` knob lets users trade memory for CPU.
- **No generational complexity** — Go got away without generations; pcc could too.

## Why pcc might NOT prefer Go-style

- **No `__del__` semantics** — Go finalizers are best-effort, eventually. Python's deterministic refcount cleanup contract is broken under pure tracing.
  - Mitigation: hybrid model — keep refcount for deterministic finalize, run tracing for cycle reclamation only. This is essentially what CPython does.
- **Multi-threading prerequisite** — single-threaded pcc would lose the "concurrent" property and pause-time benefit.
- **Pointer bitmap requires precise typing** — pcc must know which fields in each `PyObject` are pointers vs scalars. RM-P5 typed-int work + future codegen typing would feed this.

## Reference: Go's GC pacing

Go has a "GC pacer" that decides when to start the next collection. The heap target is `GOGC × live_set / 100`, and the collector is started when allocation reaches the trigger point. **This is what makes Go's GC predictable** — pcc would need an equivalent if it adopts this style.
