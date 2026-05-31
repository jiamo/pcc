# CPython original GC — refcount + generational tricolor cycle collector

**Source:** `/tmp/gc-research/python/`
- `gc.c` (66 KB) — main collector
- `gcmodule.c` (15 KB) — Python-visible `gc` module
- `gc_free_threading.c` (55 KB) — atomic-refcount variant for free-threaded build
- `pycore_gc.h` (13 KB) — internal headers + flags

## What it is

CPython runs **two collectors in tandem**:

1. **Reference counting** — every object has `ob_refcount`; every assignment / function call / scope exit does `Py_INCREF` / `Py_DECREF`. When refcount hits zero, dealloc fires immediately. This handles the bulk of garbage: non-cyclic structures.

2. **Generational tricolor cycle collector** — separate path that periodically scans **only objects that could form cycles** (containers + class instances). Three generations; objects survive a collection get promoted to the next generation. Triggered by allocation count thresholds (`gc.get_threshold()`).

The collector is **stop-the-world** within a single thread. Pre-3.13, the GIL serialized other Python threads anyway; in 3.13+ free-threaded builds (`gc_free_threading.c`), there's a separate atomic-refcount + per-thread accounting path.

## Heap layout

Every "container-like" object (list, dict, tuple, instance, function, frame, etc.) is preceded in memory by a `PyGC_Head`:

```c
// pycore_gc.h
typedef struct {
    uintptr_t _gc_next;   // next pointer in generation linked list
    uintptr_t _gc_prev;   // prev pointer; low 2 bits = collecting flag
} PyGC_Head;
```

Objects are linked into one of 3 doubly-linked lists (one per generation). Atomic types (int, str, float, bytes) are NOT in this list — they can't form cycles.

## Hot paths

### Allocation
```c
// PyObject_GC_New / PyObject_GC_NewVar — wraps malloc + threads object
// onto generation-0 list. Increments alloc counter; if over threshold,
// triggers automatic collection.
```

### Write barrier
**None.** Refcount is the write protocol: `obj.field = x` decompiles to:
```c
Py_INCREF(x);
old = obj->field;
obj->field = x;
Py_DECREF(old);
```

No GC-aware barrier needed because refcount immediately reclaims the unreferenced old value.

### Mark / sweep (cycle collection)

The collector's algorithm (`gc.c::gc_collect_main`):

1. **`update_refs`** — for each object in target generations, copy `ob_refcount` into `_gc_prev` (using its high bits). This is the "external refcount counter".
2. **`subtract_refs`** — for each object O in the generation, traverse O's outgoing references; for each child C also in the generation, decrement C's `_gc_prev` counter. After this pass, `_gc_prev` for each object equals "refs from outside the collected set".
3. **`move_unreachable`** — objects whose `_gc_prev > 0` are externally reachable; they and their transitive descendants stay. The rest are unreachable.
4. **Finalizer dispatch** — call `__del__` on the unreachable set (PEP 442 ordering: weakrefs cleared first).
5. **`delete_garbage`** — actually free.

### Generations

Promotion is age-based: objects that survive a generation-N collection move to generation N+1. Generation thresholds default to `(700, 10, 10)`: gen-0 collects every 700 allocations; gen-1 every 10 gen-0 collections; gen-2 every 10 gen-1 collections.

## Pause / latency profile

- **No concurrent phase.** Collection is single-threaded under the GIL.
- **Pause time is roughly linear** in the size of the generation being collected. Gen-0 is fast (small); gen-2 walks the entire long-lived heap.
- **No moving** — `id(obj)` stable for the object's lifetime.
- **Free-threaded build** (`gc_free_threading.c`) adds atomic refcount + per-thread mutator stop, but algorithm shape is unchanged.

## Mapping to pcc's 12 hooks

| pcc hook | CPython equivalent |
|---|---|
| `pcc_gc_alloc(size, type, flags)` | `_PyObject_GC_New` (heap object), or `PyObject_Malloc` (atomic types — no GC head) |
| `pcc_gc_retain(o)` | `Py_INCREF(o)` — atomic in free-threaded |
| `pcc_gc_release(o)` | `Py_DECREF(o)` — calls `_Py_Dealloc` on last ref |
| `pcc_gc_load_ptr(owner, slot)` | direct load — no barrier |
| `pcc_gc_store_ptr(owner, slot, val)` | inlined `Py_INCREF(val); old=*slot; *slot=val; Py_DECREF(old)` |
| `pcc_gc_store_root(slot, val)` | same pattern; "root" no different from "field" in refcount model |
| `pcc_gc_frame_enter` | no-op — refcount doesn't need stack roots |
| `pcc_gc_frame_leave` | no-op |
| `pcc_gc_safepoint` | no-op (single-threaded under GIL) |
| `pcc_gc_collect(reason)` | `_PyGC_Collect` — runs gen-N or all generations |
| `pcc_gc_pin(o)` / `unpin(o)` | no-op — never moves objects |

**Verdict:** **CPython is essentially today's pcc backend** at the architectural level. pcc's existing `py_incref` / `py_decref` + a real cycle collector at G1 = a full CPython-style backend. The hook surface is already shaped for it.

## Porting to pcc as G6.5 backend

This is the **easiest** of the five — pcc is already 80% there:

| Component | Status in pcc |
|---|---|
| Refcount machinery | ✅ done (`py_obj.c::py_incref/py_decref`) |
| Type-specific dealloc | ✅ done (15 types) |
| Immortal flag | ✅ done (PY_FLAG_IMMORTAL) |
| Tagged ints (atomic, untracked) | ✅ done |
| `PyGC_Head` linked list | ❌ missing — needs `PY_FLAG_GC_TRACKED` to link into a real generation list |
| `update_refs` / `subtract_refs` / `move_unreachable` | ❌ missing — this is G1 |
| Generations (3 of them) | ❌ no generations — could land as G6 (region allocator) |
| `__del__` dispatch | ❌ missing — G2 |
| weakref clearing before `__del__` | ❌ missing — G3 |
| `gc.collect()` Python-visible API | ❌ missing — G5 |

**Estimated work to land as `gc_cpython_style.c` backend:**
- 2 weeks for G1 single-generation tricolor (no generations, just one collected list)
- 1 week for G2 finalizer dispatch
- 1 week for G3 weakref invalidation
- 2 weeks for generations (G6 region allocator first, then promote/demote logic)
- Total: **5-6 weeks** to a fully CPython-compatible refcount + generational backend

This is what the existing G0-G5 plan in `gc-semantics-gap.md` already covers. **G6.5 abstraction layer just lets us ship this as `gc_cpython.c` + leave room for the others.**

## Why pcc may want to ship this even though "we already have refcount"

What's labeled "already have" is **only the refcount path**. The cycle collector half doesn't exist. If a Python program creates a cycle (which user code does inadvertently — closures over locals, doubly-linked structures, etc.), pcc today **leaks**. CPython doesn't.

Until G1 lands, pcc is a strictly worse Python implementation than CPython on memory management. This is THE most important GC work to ship — even before considering exotic alternatives.
