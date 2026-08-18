# `str.find` was O(n) in its own result conversion — stage2 2.49x

## Symptom

`stage2` (pcc1 compiling pcc) took **1123 s** against **71 s** for `stage1`
(CPython compiling the same source) — pcc1 roughly 15x slower than the
interpreter it is supposed to replace, which undercuts "write python run
native" outright.

## Root cause [CONFIRMED]

A flame graph of a `--pcc-self-backend-split-worker` during stage2 put **95.6%
of its CPU in one runtime function**: `py_str_accessors__utf8_codepoint_count`,
reached from `split_self_backend_ir_module_for_object_shards`.

`str.find()` locates a byte offset, then converts it to a Python codepoint index
via `_byte_offset_to_cp_offset`, which **counts codepoints from the start of the
string**. That is O(n) *on top of* the search, so N searches over one string are
O(N·n). CPython indices are O(1), so the same code is linear there.

Probe under pcc1, 688 KB string:

```
len() x200          0 ms      cached, O(1)
splitlines()        3 ms
join() / strip()    0 ms
slice x200          0 ms
find() x200       107 ms      0.535 ms per call   <-- the outlier
```

## Fix

An all-ASCII fast path: `cp_len == byte_len` means every byte is its own
codepoint, so the byte offset already *is* the codepoint offset. `_str_cp_len`
already caches the count, making the check O(1) after first use. IR text, source
text and symbol names are pure ASCII — the compiler's own workload is the common
case.

This is not a new idea: the **reverse** direction
(`utf8_byte_offset_for_codepoint`) already had exactly this fast path. Only the
forward direction was missing it. Applied to both the pcc-Python port and the C
mirror.

## Result

```
phase                              before      after     change
compile_python_total               1117.1 s    449.0 s    2.49x
  link_self_backend_ir_texts        784.8 s    131.6 s    5.96x
    link_self_native_split_workers  640.8 s     13.0 s   49.20x
    link_self_emit_objects_host     645.0 s     16.5 s   39.16x
  multi_frontend_codegen_parallel   201.5 s    188.3 s    1.07x
  link_self_pcc_driver              139.2 s    114.8 s    1.21x
```

stage2 wall clock **1123 s -> 455 s**. The phase that was not touched moved 1.07x,
which is what makes the attribution credible rather than global noise.

## The mistake this exposed, which is the more important half

`rename_llvm_global_refs` had just been "optimised" by replacing a
character-by-character scan with repeated `str.find()` jumps, and reported as a
**6.4x win measured on the host**. It was never measured under pcc1. It is:

```
286 KB module, pcc1        character loop    find-based
before the ASCII fast path      65 ms          405 ms     6.2x SLOWER
after                           52 ms           12 ms     4.3x faster
```

So a change measured 6.4x faster on CPython was **6.2x slower in the environment
that actually matters**, and it is what put `_utf8_codepoint_count` at 95.6% of
the split worker. The host and pcc1 disagree not by a constant factor but by
complexity class, because CPython's `find` is C-level and its indices are O(1).

**A host measurement cannot accept a change aimed at pcc1.** This file exists
because that rule was already written down and was still violated.

## Semantics

pcc1 output matches CPython line for line on mixed-width text (CJK, accented
Latin, indexing, slicing, `rfind`). Regression tests in
`tests/python/test_str_byte_offset_ascii_fast_path.py` (3 passed), including a
string that is ASCII only *after* a multibyte prefix — taking the fast path
there would report byte offsets as codepoint offsets. Gates: 63 passed.

## Status

[CONFIRMED] stage2 2.49x, byte-offset semantics unchanged. Memory is unaffected
(21.5 GB total / 16.2 GB in the coordinator) and remains the open problem.

## Update — the remaining gap is structural, measured

After the fix, a clean apples-to-apples comparison (same input file, neither
compiler hitting a cache, frontend only via `--emit-llvm`):

```
host CPython   2.47 s
pcc1          24.42 s      9.89x slower
```

This is not a hotspot. A flame graph of that exact pcc1 run is **flat**: the
hottest single entry is 2.8% and the top twelve sum to ~13%, all of it GC
bookkeeping —

```
managed-pointer index probes (find_slot / index_contains / pointer_is_managed)  ~6.9%
minor-graph and object-graph lock/unlock                                        ~3.5%
incref / decref                                                                 ~2.3%
store_root                                                                       0.7%
```

CPython pays none of these. The gap is a per-operation tax spread over
everything, so no single change closes an order of magnitude. The graph lock
already has a re-entrant fast path and a recorded round of TLV optimisation, so
that avenue is spent.

Two structural levers, both already pointed at by data in this file:

1. **Stop asking the provenance question.** Every "is this pointer managed?"
   is a hash probe into a process-wide index. Where codegen already knows a
   value came from a runtime allocation, the check is redundant — but no
   unchecked store variant exists (`pcc_gc_store_ptr` is the only entry point).
2. **Stream the coordinator's IR text.** The coordinator holds every module's IR
   at once — 16.2 GB of the 21.5 GB peak, and 50.5% of its own samples are in
   `read`/`fread`. Handing shards to workers without retaining them is what
   moves memory, and neither this nor lever 1 was attempted.

Also measured and set aside: host and pcc1 emit different IR for the same input
(19.76 MB vs 15.51 MB). Characterised — host emits 32006 more `bitcast`
instructions because it builds through llvmlite while pcc1 uses its own builder;
with opaque pointers those are no-ops. Not a semantic divergence.

## Status of the wider goal

```
stage2 runs at all      yes, 455 s (was: could not produce pcc2)
stage2 2.49x faster     yes, attributed by phase
pcc1 faster than host   NO -- 9.89x slower, structural, quantified above
memory reduced          NO -- 21.5 GB / 16.2 GB unchanged, lever 2 untouched
5GC matrix              NOT RUN -- backend 0 was started and stopped
```

## Update — reordering the provenance disjunction [DENIED]

Lever 1 was tried in its cheapest form first: `_pointer_is_managed_no_lock`
tested the four interned singletons (`py_None`, `py_NotImplemented`, `py_True`,
`py_False`) *before* the managed-pointer index probe, so every attribute access
and method dispatch paid four `global_load_ptr`s plus compares to rule out
values it almost never holds. The file's own comment licenses the move — "a
disjunction of side-effect-free lookups, so any order returns the same answer,
but the costs differ by an order of magnitude" — and that is exactly the
argument that had already pushed `pcc_capi_is_type_object_value` later.

Moved after the probe, mirrored in the C oracle, rebuilt:

```
pcc1 frontend on one real module   before 24.42 s   after 24.34 s   1.00x
```

**No effect.** Reverted in both mirrors. The 6.9% the profile attributes to this
area is the **index probe itself**, not the tests in front of it — four global
loads are noise against a hash probe plus the lock/unlock pair around it.

What this rules out: the cheap reordering variants of lever 1 are exhausted. A
real win has to remove the probe, not reorder around it — which means codegen
emitting an unchecked store where provenance is statically known, and that needs
a new runtime entry point (`pcc_gc_store_ptr` is currently the only one) plus a
frontend analysis that proves the value came from a runtime allocation.

## Update — halving the provenance probes on the hottest path [small, at the noise edge]

`_ptr_is_instance` paid the provenance question **twice**: once for the instance,
then again for the class pointer it reads out of the instance's `cls` slot. Each
is a lock plus a managed-pointer hash probe, and this runs on every attribute
access and method dispatch.

The second probe is redundant by an object-model invariant already documented in
AGENTS.md: `inst+16` is a *borrowed, uncounted* class pointer written by
`py_instance_new` from a validated class, and classes carry `PY_FLAG_IMMORTAL`.
Once the instance is known managed, its class is too.

Worth stating plainly: the probe was not buying safety against the failure mode
this repo actually recorded. A stray over-release that frees a class and lets
its address be reused answers "managed" from the index as well — so the probe
only excluded a genuinely foreign pointer, which that slot cannot hold. The
relocation read barrier is kept, which is what the moving backends need.

Added `_ptr_is_class_of_validated_instance` (port) /
`class_of_validated_instance_is_class` (C oracle) and routed the instance path
through it.

```
pcc1 frontend, one real module   baseline 24.34 s   after 23.59 / 23.57 s   1.03x
within-build spread                                 1.0%
still behind host                                   9.55x  (host 2.47 s)
```

**Reported as small and at the edge of what the measurement supports**, not as a
confirmed win: the 3% gap is larger than the 1% within-build spread but the
baseline came from an earlier window, and this session has measured ~0.8 s of
machine drift between windows. The change is kept on the strength of the
invariant argument — it removes provably redundant work — not on the strength of
that number.

Gates: 66 passed (module-try, ASCII fast path, multi-file, IR parity). GC
semantics and finalizer corners pass on backends 1, 3 and 4 — 17 each — which is
what matters for a change that touches a header read on a moving collector.

## Cumulative status against the goal

```
stage2 runs                  yes, 455 s (was: could not produce pcc2 at all)
stage2 faster                yes, 2.49x, attributed by phase
pcc1 faster than CPython     NO -- 9.55x behind, structural
memory reduced               NO -- 21.5 GB / 16.2 GB, lever 2 untouched
5GC matrix                   NOT RUN
```

Five attempts at the per-operation tax now: signature cache 1.13x [CONFIRMED],
`_text_lines` and `str(Type)` [DENIED], singleton reordering [DENIED], this one
1.03x at the noise edge. The pattern is consistent and worth recording as a
conclusion rather than a series of failures: **the tax is not concentrated
anywhere.** Closing an order of magnitude needs codegen to stop emitting the
checks — a static provenance analysis plus unchecked runtime entry points — or
the value model to stop allocating GC-tracked objects for short-lived values.
Nothing smaller has moved it.
