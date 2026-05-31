# Lua 5.4 GC — incremental tricolor + optional generational

**Source:** `/tmp/gc-research/lua/` (Lua 5.4.7)
- `lgc.c` (57 KB) — collector
- `lgc.h` (6 KB) — interface + state machine
- `lstate.h` (15 KB) — `global_State` + `lua_State` (host)
- `lobject.h` (23 KB) — value types + `GCObject` header

## What it is

Lua 5.4 ships **two collectors** in the same codebase, switchable at runtime:

1. **Incremental tricolor** (default) — single-generation mark-sweep, executed in small steps interleaved with mutator. Each step does a bounded amount of work, parameterized by `GCStepMul` and `GCStepSize`.
2. **Generational** (opt-in via `collectgarbage("generational", minor_mul, major_mul)`) — minor collections scan only recent allocations; major collections do a full sweep.

The tricolor invariant is documented in `lgc.h`:

> "a black object can never point to a white one. Moreover, any gray object must be in a 'gray list' (gray, grayagain, weak, allweak, ephemeron) so that it can be visited again before finishing the collection cycle."

Both collectors are **stop-the-world within Lua's single OS thread** but **fine-grained**: the work is sliced into steps, and steps interleave with bytecode execution.

## Heap layout

Every collectable object inherits from `GCObject`:

```c
// lobject.h
#define CommonHeader	struct GCObject *next; lu_byte tt; lu_byte marked

typedef struct GCObject {
    CommonHeader;
} GCObject;
```

- `next` — singly-linked list, ALL collectable objects in a single global list.
- `tt` — type tag (string, table, userdata, function, thread, etc.).
- `marked` — color bits + flags. The 8 bits encode the tricolor color, "old object" (generational), "fixed" (uncollectable), "tested" (white-A vs white-B for sweep), and finalize-on-collect.

Two **white** flags alternate per cycle: white-A and white-B. Whatever is white-N at sweep time is freed; the just-allocated white is N's complement. This avoids repainting the entire heap each cycle.

## State machine

The collector walks through 9 states (`lgc.h`):

```c
#define GCSpropagate    0   // propagate marks gray→black
#define GCSenteratomic  1   // start atomic phase
#define GCSatomic       2   // remark + finalizer queue (STW)
#define GCSswpallgc     3   // sweep main object list
#define GCSswpfinobj    4   // sweep finalizable list
#define GCSswptobefnz   5   // sweep "to-be-finalized" list
#define GCSswpend       6   // sweep cleanup
#define GCScallfin      7   // run __gc finalizers
#define GCSpause        8   // collector idle
```

Each `luaC_step()` call advances the state machine by some quantum of work. The atomic phase (`GCSatomic`) is the only true stop-the-world step; everything else is interruptible.

## Hot paths

### Allocation
```c
// lmem.c::luaM_realloc_ — wraps user-supplied allocator
// On alloc: increment GCdebt; if GCdebt > 0, trigger luaC_step()
//   (debt-based pacing; no fixed thresholds)
```

### Write barrier (forward + backward)
Two flavors (`lgc.h::luaC_objbarrier`, `luaC_barrierback`):

- **Forward barrier**: when a black object gets a new pointer to a white one, the white object is colored gray (preserves invariant).
- **Backward barrier**: for tables (which are typically heavily mutated), the table is set back to gray and added to the `grayagain` list. Cheaper for hot mutation; pays at next collection step.

```c
// lgc.h
#define luaC_objbarrier(L,p,o) (  \
    (isblack(p) && iswhite(o)) ?  \
    luaC_barrier_(L,obj2gco(p),obj2gco(o)) : cast_void(0))
```

### Incremental marking
The collector runs in **steps** — each step processes a bounded number of bytes off the gray list before yielding back to the mutator. `GCStepMul` controls how much work per allocated byte.

### Finalizers (`__gc` metamethod)
Objects with `__gc` get queued to a separate `tobefnz` list during sweep. After sweep, `luaC_runafewfinalizers` runs them one at a time, with full mutator interleaving.

## Generational mode (Lua 5.4 addition)

```c
// lgc.c::luaC_runtilstate handles generational state separately
// Two new states: "minor" and "major"
// Minor collection: only scan objects allocated since last collection
// Promotion: surviving objects → "old" generation
```

Separate gray lists per generation; old-gen objects checked only in major collections. Significantly less CPU for short-lived workloads (web request handlers etc).

## Pause / latency profile

- **No long STW pauses** — atomic phase is short (only remark + finalizer queue), interruptible everywhere else.
- **Steady incremental load**: ~10-20% throughput cost on allocation-heavy workloads.
- **No real concurrency** — Lua is single-threaded by language design; the collector runs in the same thread as the mutator, just sliced.
- **No moving** — Lua values are addressed via `TValue` slots that contain raw pointers; moving would break embedded host code.

## Mapping to pcc's 12 hooks

| pcc hook | Lua equivalent |
|---|---|
| `pcc_gc_alloc(size, type, flags)` | `luaM_realloc_` + GC debt accounting |
| `pcc_gc_retain(o)` | **None** — Lua is tracing |
| `pcc_gc_release(o)` | **None** |
| `pcc_gc_load_ptr(owner, slot)` | Plain load — no read barrier |
| `pcc_gc_store_ptr(owner, slot, val)` | `*slot = val; luaC_objbarrier(L, owner, val)` — forward barrier |
| `pcc_gc_store_root(slot, val)` | Plain store + roots in stack are scanned each cycle |
| `pcc_gc_frame_enter(frame_map, slots)` | no-op — Lua walks the call stack to find roots |
| `pcc_gc_frame_leave(slots)` | no-op |
| `pcc_gc_safepoint()` | Implicit — every bytecode dispatch can advance GC step |
| `pcc_gc_collect(reason)` | `lua_gc(L, LUA_GCCOLLECT)` — full cycle |
| `pcc_gc_pin(o)` | "fixed" flag in `marked` — set via `luaC_fix` |
| `pcc_gc_unpin(o)` | unset fixed flag |

**Verdict:** Lua's design is **the closest fit to a single-threaded pcc** — both are single-threaded, both don't move objects, both have a fine-grained step-based collector. The hook set maps cleanly.

## Porting to pcc as G6.5 backend

Lua's design is the **easiest non-refcount alternative** for pcc:

| Component | Estimate | Notes |
|---|---|---|
| Add `next` pointer + `marked` byte to `PyObjectHeader` | 1 week | Header growth — semver event for runtime ABI |
| Forward write barrier in codegen | 1 week | New IR sequence at every `pcc_gc_store_ptr` |
| Tricolor state machine | 2 weeks | 9 states from Lua; could simplify to 5 |
| Gray list + propagate phase | 1 week | Walk gray list; turn gray→black; queue children |
| Sweep phase | 1 week | Walk global object list; free white; flip white-A/B |
| Finalizer queue (`__gc`/`__del__`) | 1 week | Reuse existing G2 finalizer dispatch |
| Step-based pacer (debt accounting) | 1 week | Triggered by allocator on each `pcc_gc_alloc` |

**Total estimate:** 8 weeks for "Lua-style incremental tricolor" backend.

Add 4 more weeks if also implementing generational mode.

**Why this might be the right next backend after refcount:**
- Single-threaded — no pcc multi-threading prerequisite
- Non-moving — all of pcc's existing FFI / id-stability assumptions hold
- Step-based — no global STW pauses
- Reuses existing finalizer + weakref machinery (G2 + G3)
- Simpler to verify than ZGC or Go GC

## Why this might NOT be the right backend

- **`__del__` deterministic timing breaks** — Lua's `__gc` runs eventually, not at last-ref. CPython contract is stricter.
  - Mitigation: hybrid (refcount + Lua-style cycles), same as in `gc-pluggable-backend.md`.
- **Header bytes added** — every PyObject gains 9 bytes (`next` ptr + 1 byte flag). On a heap of 10M small objects this is 90 MB. Not free.
- **Step pacer is one-knob tuning** — `GCStepMul`. Not as flexible as Go's GOGC + pacer or HotSpot's many knobs.

## Quote from `lgc.h` worth thinking about

> "macro to tell when main invariant (white objects cannot point to black ones) must be kept. During a collection, the sweep phase may break the invariant, as objects turned white may point to still-black objects."

This is THE subtle invariant that makes write barriers correct. Any collector pcc adopts must preserve it (or its analog), and the proof is non-trivial.
