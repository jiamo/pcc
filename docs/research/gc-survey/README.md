# GC research survey — 5 production GC designs

Reference designs to consider when scoping pcc's pluggable GC backend
(see `docs/issues/gc-pluggable-backend.md` for the ABI). Each entry
introduces one production GC, tracks the upstream source, and maps
its design to pcc's 12-hook G6.5a interface.

## Contents

| # | GC | Doc | Source |
|---|---|---|---|
| 1 | CPython refcount + cycle | [01-cpython-original.md](01-cpython-original.md) | `/tmp/gc-research/python/` |
| 2 | OpenJDK ZGC / GenZGC | [02-zgc.md](02-zgc.md) | `docs/refs_docs/gc-research/zgc/` (`jdk-27+21`) |
| 3 | Go (Green Tea proposal) | [03-go-greentea.md](03-go-greentea.md) | `/tmp/gc-research/go-greentea/` |
| 4 | Lua 5.4 | [04-lua.md](04-lua.md) | `/tmp/gc-research/lua/` |
| 5 | OCaml 5 multicore | [05-ocaml.md](05-ocaml.md) | `/tmp/gc-research/ocaml/` |

## Cross-comparison

| design | refcount? | moving? | concurrent? | generational? | port estimate |
|---|---|---|---|---|---|
| CPython | yes | no | no | yes (3) | **5-6 weeks** |
| ZGC | no | yes | yes | optional | **3-6 months** |
| Go | no | no | yes | no | **12-14 weeks** |
| Lua 5.4 | no | no | no (incremental) | optional | **8 weeks** |
| OCaml 5 | no | yes (compaction) | yes | yes (2) | **14-22 weeks** |

## Decision matrix for pcc

If the goal is **"pcc has a real cycle collector that doesn't break things"**:
→ Implement CPython-style as the first backend. Already 80% there.

If the goal is **"pcc gains a non-refcount option for multi-threaded workloads"**:
→ Lua-style is closest fit (single-threaded compatible, non-moving).
   Add multi-threaded later via Go-style upgrade.

If the goal is **"pcc as research platform for low-pause GC"**:
→ Go-style is the right pivot point. ZGC and OCaml are research-grade
  but require infrastructure (multi-threading, stack maps, moving)
  that pcc doesn't have today.

If the goal is **"pcc supports embedded / single-binary deployment"**:
→ Lua-style step-based is ideal. No worker threads, predictable memory.

## What every doc maps back to

The 12 hooks codex landed in `pcc_gc_*`:

```
pcc_gc_alloc       pcc_gc_collect
pcc_gc_retain      pcc_gc_release
pcc_gc_load_ptr    pcc_gc_store_ptr   pcc_gc_store_root
pcc_gc_frame_enter pcc_gc_frame_leave
pcc_gc_safepoint
pcc_gc_pin         pcc_gc_unpin
```

Each survey doc has a "Mapping to pcc's 12 hooks" section showing what
that GC would do at each hook. This is the contract pcc commits to —
any future backend must fit this shape.

**Key observation across all five:**
- `retain/release` only used by CPython style. All four tracing GCs
  treat them as no-ops. Hybrid (refcount + tracing for cycles) calls
  into both.
- `load_ptr` only meaningful under ZGC (read barrier) and concurrent
  variants. Other four make it a plain load.
- `pin/unpin` only meaningful for moving GCs (ZGC, OCaml compaction).
  Non-moving GCs ignore them.
- `frame_enter/leave` matters for all tracing GCs — they need to find
  stack roots. Refcount ignores them.
- `safepoint` is the bottleneck mechanism for all concurrent GCs
  (ZGC, Go, OCaml). Single-threaded GCs (CPython, Lua) can no-op it.

## Recommended reading order

If you're going to implement one of these:

1. Read `01-cpython-original.md` first — confirms what pcc already has
   and what's missing for the most natural starting point.
2. Then `04-lua.md` — second-easiest path, single-threaded compatible.
3. Then `03-go-greentea.md` — when pcc grows multi-threading.
4. Then `05-ocaml.md` — when pcc considers generational + multicore.
5. Then `02-zgc.md` — research / aspirational only.
