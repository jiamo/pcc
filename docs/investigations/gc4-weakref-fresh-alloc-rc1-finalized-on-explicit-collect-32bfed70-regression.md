# Investigation: backend #4 explicit collect finalizes a held (rc=1) FRESH_ALLOC weakref — 32bfed70 regression

## Status
confirmed-regression (root-caused to a commit + pinpointed to the finalize site; exact
mechanism in the relocation rework still open — filed for the relocation-rework owner)

## Problem Description
`tests/python/data_model/test_t4_weakref_native_acceptance.py::
test_t4_weakref_callable_and_dealloc_clear_native` aborts with
`py_decref: refcount underflow` (py_obj.c:822). Under backend #4
(`PCC_GC_KIND_COLORED_RELOCATING`), an explicit `pcc_gc_collect(0)` **finalizes a
weakref object the caller still holds (refcount 1)**, dropping its refcount to 0;
the caller's later `pcc_gc_release(wr)` then underflows.

## Repro (minimal C, no test harness)
```c
pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING);
PyObject *obj = py_list_new(0);            // rc 1
PyObject *wr  = py_weakref_new(obj, 0);    // rc 1, callback NULL
PyObject *before = py_weakref_call(wr);    // increfs obj -> rc 2
pcc_gc_release(before);                     // obj rc 1
pcc_gc_release(obj);                        // obj rc 0 (pending)
pcc_gc_collect(0);                          // <-- wr rc 1 -> 0 here (BUG)
PyObject *after = py_weakref_call(wr);      // returns py_None (obj reaped) — but wr was finalized
pcc_gc_release(after);
pcc_gc_release(wr);                         // py_decref underflow: wr rc already 0
```
Instrumented probe prints: `before collect: wr rc=1` … `after collect: wr rc=0`,
and `pcc_gc_note_relocation_read(wr) == wr` (wr is **not** relocated — it is
directly finalized in place).

## Bisect — confirmed regression
- Clean worktree at **cb5d37e8** (the commit before 32bfed70): probe prints `1`,
  exits 0. **Passes.**
- **32bfed70** ("5-GC self-host bootstrap gate + GC-root, relocation, zpage &
  value-class-payload rework"): underflow abort. **Fails.**

## Pinpoint
lldb hardware watchpoint on `&wr->refcount`: the 1→0 write occurs in
`pcc_gc_collect_tracing + 3512` (frame: `pcc_gc_collect_tracing` ← `pcc_gc_collect`
← `main`). That offset is the inlined `pcc_gc_sweep_unreachable` →
`pcc_gc_finalize_unreachable`, whose `h->refcount = 0` (py_gc_backend.c:6738)
precedes the type-tag dealloc switch (the `sub w8,#2; cmp #0x1e` dispatch seen at
the PC). So `wr` reached `finalize_unreachable` as a sweep candidate.

`wr` is created FRESH_ALLOC and is held only by a C local (rc=1), not pinned and
not in a GC frame, so it is not a trace root. During an *explicit* collect,
`pcc_gc_seed_roots` clears `PY_FLAG_GC_FRESH_ALLOC` (it is in the
`flags_update` mask) and colors unrooted objects WHITE → `wr` becomes a
SWEEP_CANDIDATE and is finalized despite rc=1.

## Why this is filed, not fixed here
`pcc_gc_seed_roots`, `pcc_gc_gray_current_roots`, `pcc_gc_sweep_unreachable`
(incl. its `(PINNED|FRESH_ALLOC)==0` guard), and `pcc_gc_collect` are **textually
identical** between cb5d37e8 and 32bfed70 (verified by diff). So the regression
is an *emergent* interaction from 32bfed70's relocation/zpage/object-tracking
rework (when/whether the mark cycle runs for an explicit collect, or how the
relocation set / object nodes are tracked), not a single changed line in the
obvious functions. Pinning it needs the relocation rework's owner (concurrent
in-flight work touches exactly this area).

A blanket "don't finalize rc>0" guard is **unsafe** — it would prevent reclaiming
reference cycles (cycle members legitimately have rc>0 from each other). The
correct fix must restore whatever protected an externally-held FRESH_ALLOC
newborn at cb5d37e8 (likely the mark cycle not running / not clearing FRESH_ALLOC
for this explicit-collect shape).

## Report
Confirmed-regression, filed with decisive bisect + minimal repro + finalize-site
pinpoint. No code changed (avoids guessing inside active relocation-rework
machinery and avoids the unsafe rc>0 finalize guard). Backend #4 (relocating) is
already flagged known-OPEN/experimental.
