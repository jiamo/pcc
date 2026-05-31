# Investigation: a live exception's referents (message) are reclaimed by the tracing collect (#1/#2/#3/#4)

## Status
resolved 2026-05-31 — fix landed in frontend root mapping for locals assigned
from except-handler bindings. The contract test is now a hard 0..4 gate:
`tests/python/gc_production_contract/test_exception_roots.py` -> 5 passed, and
the full common suite -> 95 passed after adding the valuebox brick.

## Problem Description
A caught exception held in a local, after `gc.collect()`, must still have its
message/args. Under `--backend self --python-libpython=off`:
```python
def main():
    saved = None
    try:
        raise ValueError([1, 2, 3])
    except ValueError as e:
        saved = e
    gc.collect()
    print(str(saved))   # CPython / #0: [1, 2, 3]   ;   #1/#2/#3/#4: <null>
```
`str(saved)` returns `PyExceptionObject.message` (py_exc_get_message). On the
tracing backends it is `<null>` after gc.collect — the message object was
reclaimed even though `saved` (the exception) is still reachable.

## Repro
```bash
printf 'def boom(p):\n    raise ValueError(p)\ndef main():\n    saved=None\n    try:\n        boom([1,2,3])\n    except ValueError as e:\n        saved=e\n    import gc; gc.collect()\n    print(str(saved))\nmain()\n' > /tmp/exctb.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/exctb.py -o /tmp/exctb_bin
for b in 0 1 2 3 4; do echo "#$b: $(PCC_GC_BACKEND=$b /tmp/exctb_bin)"; done
#   #0 -> [1, 2, 3]   |   #1/#2/#3/#4 -> <null>
```

## Test [CONFIRMED]
`tests/python/gc_production_contract/test_exception_roots.py` — #0 asserts
`[1, 2, 3]`; #1/#2/#3/#4 xfail(strict=False). Flips to xpass when fixed.

## Findings so far (offset analysis — NOT yet the full root cause)
`PyExceptionObject` (py_internal.h:720): h(16) / exc_class@16 / message@24 /
cause@32 / context@40 / traceback@48 / n_frames@56 / cap_frames@60.
`_trace_referents` exc case (py_gc_backend.py:2314) visits offsets 16,24,32,40 —
exc_class, message, cause, context — but MISSES traceback@48 (a real, separate
gap: traceback frame records + their locals are untraced). HOWEVER the repro's
`[1,2,3]` is the MESSAGE (offset 24), which IS in the trace list, AND
py_exc_objects.c stores it via the GC barrier (`pcc_gc_store_ptr(e, &e->message,
...)`). So message@24 being reclaimed despite being traced + barrier-stored is
NOT explained by a missing offset — needs LLDB:
- is `saved` (main's active-frame local) in the mark root set when gc.collect
  runs from main? (container_graph / set_graph locals ARE roots, so probably
  yes — but confirm for this shape);
- is the exception object actually GRAYED (does `_trace_referents` run on it)?
- is message@24 actually populated at raise time (vs lazily on str())?
The contrast: `test_root_graphs.py` (generator-frame / dict->list->instance /
set-of-instances) all pass on 0..4, so frame roots + dict/list/set tracing are
correct; only exception referents fail.

## Update 2026-05-31: LLDB root-cause CONFIRMED — the exception is never GRAYED (not an offset gap, not a tracking-at-alloc gap)

The "missing offset" and "untracked-at-alloc" hypotheses are both **wrong**.
Hardware-watchpoint tracing of the exception's `flags` word (`obj+12`) across a
full `gc.collect()` on **both #1 (incremental) and #3 (generational)** shows the
same thing: **the exception's color bit is WHITE (0x08) the entire collect and
is NEVER set to GRAY (0x10).** Because it stays white, `_finish_tracing_cycle`
flags it `GC_SWEEP_CANDIDATE` (0x400), and the two-phase sweep's PASS-1
`_clear_unreachable(exc)` nulls its `message@24`. The exception object itself is
kept alive by refcount (`saved`), so `str(saved)` returns a live tag-12 object
whose message is now `<null>`.

Watchpoint writers to the exc flag during collect (identical shape on #1 / #3):
```
#1: 0x4008(FRESH+white) -> _seed_roots 0x000a(white+TRACKED)
                        -> _finish_tracing_cycle 0x040a(+SWEEP_CANDIDATE, still white)
                        -> py_gc_untrack 0x0408(TRACKED cleared)
#3: 0x1088(white) -> _step_generational_promotion 0x110a -> _seed_roots 0x110a
                  -> _finish_tracing_cycle 0x150a(+SWEEP_CANDIDATE) -> py_gc_untrack 0x1508
```
`_mark_root_gray_if_known` is **never** a writer to the flag — i.e. it is entered
(confirmed earlier: `pcc_gc_collect`->`pcc_gc_step`->`_step_tracing`->
`_begin_mark_cycle`->`_seed_roots`->`_gray_current_roots`->`_gray_mapped_roots`->
`_mark_root_gray_if_known(exc)`) **but returns before its gray store** — i.e.
`_is_known_object(exc) == 0` at mark time. So the exc is a *visited frame root*
but is *not in the GC object index* when the mark runs, so it cannot be grayed.

Why is a frame-root exception not in the index at mark time? The collect-entry
baseline flag has **no `GC_FLAG_GC_TRACKED` (0x2)** on either backend, even
though `py_gc_track` ran at construction (LLDB-confirmed `_is_known_object(exc)`
returned 1 during `py_exc_alloc`'s store-barrier). The only `py_gc_untrack`
caller on this path is `py_decref` at py_obj.c:535, which runs **only when
refcount hits 0**. So between raise and `gc.collect`, the exception's refcount
transiently reaches 0 (an over-decref during propagation/catch), which
`py_gc_untrack`s it (removes it from the index + clears the flag); the surviving
reference in `saved` keeps the *memory* alive but never re-tracks it. At
`gc.collect`, the untracked-but-live exception is invisible to the mark.

Discriminators that localize this to exceptions specifically (not a general
except-handler-frame-root bug): a fresh `list` created and bound ONLY to a local
inside the `except` handler survives `gc.collect` on all five backends
(`tests/.../test_root_graphs` shape) — so except-handler locals ARE frame roots
and the frame-root machinery is correct. Only the *exception object* loses
tracking.

## Update 2026-05-31 (later): DEFINITIVE root cause — FRONTEND does not GC-root exception-bound locals (the prior "propagation untrack" conclusion was WRONG)

The prior block's "exc untracked before collect via propagation refcount-zero"
is **superseded** by direct evidence:
- `pcc_gc_collect` (py_obj.c:343) on backends 1/2/3/4 = STW ->
  `pcc_gc_begin_explicit_tracing_collect` -> loop `pcc_gc_step(1024)` ->
  `pcc_gc_collect_tracing` (SWEEP). The MARK is in the step loop
  (`pcc_gc_step`->`_step_tracing`->`_begin_mark_cycle`->`_seed_roots`->
  `_gray_current_roots`->`_gray_mapped_roots`->`_mark_root_gray_if_known`); the
  earlier mis-gate at `pcc_gc_collect_tracing` (after the mark) is why root-gray
  appeared "never called". Gate LLDB at `pcc_gc_collect`.
- `pcc_gc_alloc` (py_obj.c:223) -> `pcc_gc_note_object_allocated_sized`
  (py_gc_backend.py:4239): on backends 1/2/3/4 it links EVERY allocation into
  the object list AND `pcc_gc_object_index_insert` (line 4286). So the exception
  IS in the GC index from alloc — it does NOT need `py_gc_track`. (No.2 was
  doubly wrong.) Backend 0 returns early (no tracing list).
- `pcc_gc_note_object_freeing(exc)` + `py_dealloc_exc(exc)` fire ONLY during
  gc.collect (via `_finalize_unreachable`<-`_sweep_unreachable`), NEVER in
  propagation — so the exc's refcount does NOT transiently hit 0 in propagation.
  No.3's "over-decref" does not exist.

CONFIRMING DISCRIMINATOR (the decisive one): in ONE except handler, bind a fresh
list AND the exception to two locals, then gc.collect:
```python
except ValueError as e:
    s_list = [7, 8, 9]
    s_exc = e
gc.collect(); print(s_list, "|", str(s_exc))
# #0: [7,8,9] | [1,2,3]     #1/#2/#3/#4: [7,8,9] | <null>
```
`s_list` survives on all 5 backends; `s_exc` loses its message on the tracing
backends — SAME frame, SAME handler. So `main`'s frame IS scanned for roots
(s_list is grayed), but `s_exc` is NOT in the frame root map. LLDB confirms
`_mark_root_gray_if_known` fires for `s_list` (via `_gray_mapped_roots`) but the
exception is never passed to it.

ROOT CAUSE (frontend, not runtime/GC): a local becomes a GC root only via
`_ensure_owned_local_gc_root` (ownership_lowering.py:388), which registers the
slot in the per-frame root map. It is gated on the RHS being an OWNED (new-ref)
value (`value_is_owned`) with IR type `_CSTR`. `s_list = [7,8,9]` (fresh list =
owned) -> rooted. `s_exc = e` is a borrowed local-load copy: the assignment
increfs (s_exc owns a strong ref) but the RHS expr is "borrowed", so the
ownership gate does NOT root s_exc. Normally `x = y` is safe because `y` (or the
owner chain) stays rooted; but here the source `e` is the except-handler binding,
which is ALSO not GC-rooted — exception_lowering.py:530 does a bare
`_alloca_in_entry(_CSTR)` + store with NO `_ensure_owned_local_gc_root` and
releases its retain at handler end (:560). So after the handler nothing roots
the exception, and the tracing mark sweeps it (clears message@24, frees the exc
shell). On #0 (refcount) it survives because #0 ignores the tracing list.

FIX DESIGN (careful, bootstrap-critical — pcc/py_frontend/codegen is self-host
critical, AGENTS.md §9): the correct fix is that a local which OWNS a strong ref
must be GC-rooted regardless of whether the RHS expression was new-ref or
borrowed-then-increfed. Surgical option A (root the handler binding `e`'s slot
at :530 with `init_null=True`) is INSUFFICIENT (the repro saves to `s_exc`, not
`e`; e's retain is released at :560) AND unsafe (a handler that catches without
saving would leave e's rooted slot dangling after release -> gray freed memory).
The robust fix touches the ownership/root gate so borrowed-copy assignments that
incref a PyObject local root that local; must be scoped to avoid bloating every
function's frame map, and MUST pass the full stage1->2->3 bootstrap (#0
byte-identical) + the gc_production_contract suite (#1/#2/#3/#4 xpass on
test_exception_roots, others still green) + revert on any regression.

## Proposals
- No.1 trace ALL exception referent slots incl traceback@48  [pending — real but SECONDARY; message@24 is already in `_trace_referents`'s tag-12 case, not the repro's cause]
- No.2 `py_gc_track` the exception in `py_exc_alloc`  [DENIED — exc is already indexed by `pcc_gc_alloc` on backends 1-4; tracking is redundant, and it's a runtime fix for a frontend bug; reverted]
- No.3 fix a propagation/catch over-decref  [DENIED — there is no over-decref; `pcc_gc_note_object_freeing(exc)` fires only inside gc.collect, never in propagation]
- No.4 FRONTEND: GC-root locals that own a strong PyObject ref via a borrowed-source assignment (`s_exc = e`) and/or root the except-handler binding properly through the standard owned-local lifecycle  [CONFIRMED — scoped implementation tracks except-handler names and roots locals assigned from them]

## No.2 py_gc_track the exception in py_exc_alloc
### Code Change
Added `py_gc_track((PyObject *)e);` before `return e;` in `py_exc_alloc`
(py_exc_objects.c) and the mirror `py_gc_track(e)` + extern in the port
(py_exc_objects.py).
### DENIED
After wipe+rebuild, the repro still returned `<null>` on #1/#2/#3 (and changed
#4 to `None` — a side effect, not a fix). Watchpoint proof above: the exc is
tracked at alloc but `py_gc_untrack`ed before `gc.collect` (refcount-transient-
zero in propagation), so it is not in the index at mark time regardless of
alloc-time tracking. Reverted both edits (no unverified, non-fixing, un-
bootstrapped edit left in shared runtime, per AGENTS.md §9 + feedback_test_first).
The full fix will re-introduce alloc-time tracking *together with* the
propagation-untrack fix (No.3) in one bootstrapped change.

## Context
Third gap surfaced by the 5-GC common contract suite (after object-lifetime
use-after-free and cycle-finalizer not-run, both resolved). The suite continues
to convert the "5 production-equal backends" intent into executable checks.

## No.4 frontend roots for except-binding-derived locals
### Code Change
`pcc/py_frontend/codegen/layer1_init.py` initializes
`_except_binding_names`. `exception_lowering.py` records names introduced by
`except ... as <name>`. `assignment_statement_lowering.py` detects the narrow
shape `target = <except-binding-name>` for object-pointer locals and registers
the target slot with `_ensure_owned_local_gc_root`; other borrowed-copy
assignments keep the previous root policy.

This avoids rooting the handler binding itself after the handler retain is
released, while still rooting the local that owns the surviving strong
reference (`saved = e`). The implementation is intentionally scoped to
except-binding-derived locals rather than broadening every `x = y` borrowed-copy
assignment.

### CONFIRMED
- `tests/python/gc_production_contract/test_exception_roots.py` -> 5 passed.
- `tests/python/gc_production_contract` -> 95 passed after the valuebox roots
  brick was added.

### Report
The original exception-message loss on tracing backends #1/#2/#3/#4 is closed.
The root cause was frontend frame-root metadata, not runtime exception tracing
or allocation tracking. Remaining exception traceback/frame-root breadth should
be covered by a separate contract brick (`test_exception_traceback_roots.py`)
instead of reopening this message-referent bug.
