# Where pcc1 native code beats CPython and where it loses, measured per operation

## Why this exists

stage2 (pcc1 compiling pcc) runs ~10x slower than stage1 (CPython compiling the
same source), which contradicts "write python run native". Profiles of stage2 are
**flat** — the hottest frame is 2.8%, the top twelve sum to ~13% — so there is no
hotspot to fix. A flat profile means per-operation cost, and that has to be
measured per operation rather than searched for in a call graph.

## Measurement

Same source, N = 300000, one process each, no compiler involvement in the timing
loop (`tests`-free micro benchmark, `pcc1 -o` native binary vs `python3`).

```
operation   CPython   pcc1 native   result
int_loop      14 ms         0 ms    pcc1 faster by >=14x
calls         10 ms         2 ms    pcc1 faster by 5x
list          16 ms        49 ms    pcc1 SLOWER by 3.1x
dict          28 ms        80 ms    pcc1 SLOWER by 2.9x
attr          11 ms        35 ms    pcc1 SLOWER by 3.2x
str           26 ms       154 ms    pcc1 SLOWER by 5.9x
```

## What this says

**The thesis holds for scalar code and fails for heap objects.** Integer
arithmetic in a loop compiles to essentially nothing, and a call is 5x cheaper
than an interpreted one — that is the native win the project is built on. Every
operation that touches a heap object is 3-6x *worse* than the interpreter,
because pcc-compiled code pays two taxes CPython does not:

* a managed-pointer index probe per provenance question
  (`pcc_gc_managed_pointer_find_slot`, `pointer_is_managed`, `index_contains`),
* a refcount/GC barrier per pointer store (`pcc_gc_store_ptr`, incref/decref,
  and the minor-graph lock even in a single-threaded process).

A compiler's own workload is almost entirely strings, dicts, lists and attribute
access, so stage2 lands squarely in the losing column. That is the mechanism
behind the ~10x, and it explains why five separate emitter micro-optimisations
this session produced 1.13x, 0.98x, 0.99x, -2.2% and -3.2%: they were shaving
constants off operations whose cost is structural.

`str` is the worst at 5.9x and the compiler is string-heavy, which makes it the
highest-leverage target.

## What follows

Closing a 3-6x per-operation gap is not hotspot work. The levers, in order of
expected leverage:

1. **Static provenance.** The index probe answers "is this pointer ours". When
   codegen knows a value came from a runtime allocation, the question is already
   answered and the probe is pure overhead. This needs an unchecked barrier
   variant plus frontend proof obligations; no such variant exists today
   (`py_runtime.h` declares only `pcc_gc_store_ptr`).
2. **Single-threaded lock elision.** `pcc_py_gc_minor_graph_lock` already has a
   re-entrant fast path, but still issues an atomic CAS with one thread live.
3. **The value model** (`int` tagged lane already works — that is why int_loop
   wins). Extending identity-free payloads to short-lived strings would move
   the worst case, but it is the largest piece of work.

## First fix from this diagnosis: literal concatenation was not folded

Breaking `str` down by component found one cost that is *not* structural:

```
component          CPython   pcc1 before   pcc1 after
str(i)               20 ms         80 ms       59 ms
literal concat        7 ms         95 ms       26 ms   13.6x -> 3.7x gap
len(constant)         8 ms          1 ms        1 ms   (pcc1 already 8x faster)
"item" + str(i)      26 ms        139 ms      123 ms
```

CPython's peephole folds `"a" + "b"` at compile time, so a loop containing a
literal concatenation costs it nothing, while pcc allocated and concatenated on
every iteration. `_fold_str_literal_concat` in `expr_dispatch_lowering.py` folds
`StrLit + StrLit` to one `StrLit`, keeping the left span and its inferred type.

Deliberately narrow: **both** sides must already be string literals. Anything
else can carry a user `__add__` or a type known only at runtime. Chained
literals fold left-to-right because the inner `BinOp` meets the same check on
the way down. The fold joins decoded *values*, so escapes and multibyte text
survive — a fold over raw source text would not, and a test pins that.

Tests: `tests/python/test_str_literal_concat_folding.py`, 3 passed, covering the
results, the dynamic-operand boundary, and escapes/CJK. Gates: 69 passed.

Note `len(constant)`: pcc1 is **8x faster** than CPython there, because the
codepoint length is cached on the string object. Where the object model already
has the answer, native wins.

## Status

Diagnosis [CONFIRMED] and quantified per operation. One component fix landed
(literal folding, 13.6x -> 3.7x on that component). The scalar result is worth
stating positively: on the code the value model already covers — integer
arithmetic, calls, cached string length — pcc1 beats CPython outright. The
remaining 3-6x on list/dict/attr/dynamic-str is the structural tax and needs
static provenance or the value model, not constant folding.

## Where the per-operation tax actually sits: root/pin, not the provenance probe

Profiling the `list` benchmark itself (not the compiler) reallocates the blame:

```
pcc_gc_store_root      17.5%
pcc_gc_unpin           11.9%
pcc_gc_load_ptr        10.0%
pcc_gc_pin              7.8%
the benchmark's work     5.6%   <-- bench() itself
incref + decref          7.4%
pointer_is_managed       3.0%   <-- the provenance probe is small
frame_leave_lifo         2.6%
```

GC machinery is ~68% of the loop and the work is 5.6%. **The dominant cost is
root/pin bookkeeping, not the managed-pointer index probe** — which corrects the
earlier plan in this file: removing the probe entirely would be worth ~1.07x.

The emitted IR shows why. One `lst.append(i)` in a loop body:

```
while.body.27, 38 IR lines:
  pcc_gc_load_ptr          9
  pcc_gc_pin               4
  pcc_gc_store_root        4
  pcc_gc_frame_enter_lifo  2
  pcc_gc_frame_leave_lifo  2
  pcc_gc_unpin             2
  py_list_append           1   <-- the work
```

23 GC operations per append, including pinning and rooting `i` — and the same
root is reloaded three times in a row (`%.46`, `%.47`, `%.48`).

## What must NOT be done here, and why

The obvious fix — skip the root when the argument's static type is `int` — is a
**GC safety bug**. `int` is arbitrary-precision in this project: small values ride
the tagged lane, large ones are heap bignums that must be rooted. Keying the
elision on `IntType` would drop the root for bignums, which is exactly the
"weaken GC for speed" move the repository forbids.

## What was landed, and its measured (null) effect

`list_method_lowering.py` now skips the temporary root when the item is in the
existing `_value_is_never_gc_object` registry — values literal lowering has
already *proven* to be tagged immediates. That is the only safe elision
available without new analysis, and it reuses a mechanism that already existed
(the registry's own docstring says pin/unpin/store_root are no-ops for these
values).

Verified: identical output to CPython for tagged-boundary
(`4611686018427387903`), bignum (`1 << 200`), string, nested list and variable
appends. IR: `pin` 22 -> 17, `unpin` 35 -> 26 in the benchmark function.

**Measured effect on the benchmark: none** (list 49 ms -> 48 ms, inside noise).
The loop appends a *variable*, so the proven-tagged registry does not cover it.
The change is correct and reduces emitted calls for literal appends; it does not
touch the case that dominates.

Closing the loop case requires emitting an **inline tagged test with a branch**:
fast path uses the value directly, slow path keeps the full root. That is safe
(the slow path is unchanged) but it is a basic-block-and-phi change in a hot
shared codegen path, and this session has already broken pcc1 four times with
smaller edits. It needs its own slice with stage1 + stage2 verification.

## Current standing against CPython

```
operation   CPython   pcc1   result
int_loop      14 ms    2 ms  pcc1 7x faster
calls         10 ms    5 ms  pcc1 2x faster
len(const)     8 ms    1 ms  pcc1 8x faster
list          17 ms   48 ms  2.8x slower
dict          30 ms   79 ms  2.6x slower
attr          12 ms   34 ms  2.8x slower
str           28 ms  126 ms  4.5x slower (was 5.9x before literal folding)
```

## Second fix: store_root was calling refcount helpers that always return early

`pcc_gc_store_root` on the refcount backend did `old = *slot; py_incref(value);
*slot = value; py_decref(old)`. Both helpers return immediately for a tagged
immediate or NULL, so for those values the two calls are pure overhead — and
codegen emits roughly **47000 store_root sites**. The slot write is
unconditional; only the no-op calls are elided. Applied to the pcc-Python port
and the C mirror.

```
operation   CPython   before   after   change
int_loop      14 ms     2 ms    1 ms
calls         10 ms     5 ms    2 ms
list          17 ms    48 ms   30 ms   -37%, gap 2.8x -> 1.8x
dict          29 ms    79 ms   72 ms   -9%
str           27 ms   126 ms  124 ms   unchanged
attr          11 ms    34 ms   35 ms   unchanged
```

Tests: `tests/python/test_gc_store_root_tagged_fast_path.py`, **10 passed** —
two cases across all five GC backends, mixing tagged ints, a bignum, strings and
nested containers through rooted slots and forcing `gc.collect()`, so a root
that was wrongly skipped loses its object and the test fails. Full gate set: 76
passed.

**A single run reported the opposite conclusion.** The first measurement after
the rebuild read `list 38 / dict 83 / str 145` and suggested the change helped
lists but cost 15% on strings — a plausible story (for a real pointer the added
tests duplicate what py_incref does internally). Three consecutive runs of the
same binary read `30/73/121`, `29/71/125`, `32/72/126`: the first run carries
cold-start cost and the "string regression" did not exist. **A benchmark run once
is not a measurement**, and the wrong story it told was mechanically credible,
which is what makes it dangerous.

## What is left, and why the runtime side is now exhausted

```
pcc_gc_pin / pcc_gc_unpin   19.7% of the list loop -- already two early-return
                            tests plus two memory ops; nothing left to shave.
                            The cost is CALL COUNT, decided by codegen.
pcc_gc_load_ptr             10.0% -- on backend 0 it is a null test, one load
                            and one global flag test.  Also minimal.
_gc_pin / _gc_unpin         codegen already elides these for values proven
                            non-GC, but the registry only covers literals, so a
                            loop over a variable is not covered.
```

The remaining per-operation gap therefore needs codegen to emit **fewer** GC
operations, not cheaper ones: an inline tagged test with a branch (fast path
skips pin/root/reload, slow path unchanged), or hoisting loop-invariant roots.
Both are basic-block changes in a hot shared path and belong in their own slice.

## The runtime side is exhausted; the remainder is emitted call count

Profiling the `str` workload (`"item" + str(i)`; `len(s)`) after the two runtime
fixes gives, over 1581 samples:

```
long tail (other)              41.9%
root/pin bookkeeping           18.0%
graph lock / unlock            12.8%
managed-pointer index          10.9%
refcounting                     5.9%
the actual string work          5.4%   <-- py_str_* / py_int_*
per-operation config/type checks 4.9%
--------------------------------------
GC bookkeeping total           52.6%
```

Every one of those runtime helpers has now been read and is already minimal:

```
pcc_gc_store_root   fixed this session -- tagged/NULL no longer call incref/decref
pcc_gc_pin/unpin    two early-return tests plus two memory ops; nothing to shave
pcc_gc_load_ptr     on backend 0: null test, one load, one global flag test
minor_graph_lock    one TLV address fetch and one read; re-entry returns
                    immediately, only the first entry does a CAS.  Its own
                    comment records an earlier round of optimisation on exactly
                    this leaf ("the single hottest leaf in a pcc1 -> pcc2
                    frontend worker").
frame roots         `pcc_gc_backend0_frame_roots_enabled` defaults to 0, so
                    backend 0 already skips frame-node creation entirely.
```

So the cost is **how many times codegen emits these calls**, not what each one
costs. That is a codegen problem and it is the only remaining lever:

```
inline tagged test + branch   fast path skips pin/store_root/reload; slow path
                              unchanged.  Handles variables, which the existing
                              `_value_is_never_gc_object` registry cannot -- it
                              only covers literals.
hoist loop-invariant roots    `lst` in `while ...: lst.append(i)` never changes,
                              yet the body re-roots it every iteration.
drop redundant reloads        the emitted body reloads the same root three times
                              in a row with no GC point between.
```

All three are basic-block-level changes in a hot shared path. This session broke
pcc1 four times with smaller edits, so each belongs in its own slice with a
`print("hi")` health check, a stage1 build, and a pcc1 benchmark before it is
believed.

## Session standing

```
operation   CPython   pcc1 start   pcc1 now   note
int_loop      14 ms        0 ms       1 ms    pcc1 14x faster
calls         10 ms        2 ms       2 ms    pcc1 5x faster
len(const)     8 ms        1 ms       1 ms    pcc1 8x faster
list          17 ms       49 ms      30 ms    2.9x -> 1.8x slower
dict          29 ms       80 ms      72 ms    2.8x -> 2.5x slower
attr          11 ms       35 ms      35 ms    3.2x slower
str           27 ms      154 ms     124 ms    5.7x -> 4.6x slower
```

Landed and verified this session: module-level `try` lowering (stage2 went from
unable to produce pcc2 at all to producing it), the `str.find` codepoint
conversion fix (stage2 1123 s -> 455 s, 2.49x, attributed by phase), literal
concatenation folding, the `store_root` tagged fast path (list -37%), and repairs
to five GC test files including one genuine gap (`PY_TYPE_VTHREAD_CHANNEL` had
pointer slots but no slot classification).

## [DENIED] Reusing an already-loaded root value to skip one reload

`_leave_container_temp_root` re-reads the slot to get the value it unpins, and
its caller in `list_method_lowering` has just read the same slot for the release
— with no GC point in between. Threading the value through as an optional
`current=` argument is safe and removes that reload.

Measured on the list benchmark's `bench` function: **`pcc_gc_load_ptr` 40 -> 39**.

Reverted. One call removed does not justify an optional parameter whose contract
("the caller asserts there is no GC point between") has to be honoured by every
future caller of a helper on a hot shared path. The three back-to-back loads seen
in the emitted loop body are not all from this helper — most come from separate
statements that each re-read their own slot, so the win has to come from a pass
that eliminates redundant loads generally, not from hand-threading values
between two specific functions.

Recorded so the next attempt does not re-derive the same 1-call result.

## The long tail examined: object lifecycle, not a hidden hotspot

The 41.9% previously filed as "other" was never opened. Expanded, it has no item
above 2.3% — it is genuinely flat — but it groups:

```
object lifecycle                     ~10.6%
  pcc_gc_alloc 1.45   malloc 1.08   free 1.33   free_object_memory 1.27
  note_object_freeing 1.96   py_gc_untrack 1.52
  weakref_invalidate 1.08   identity_index_remove 0.95
per-operation state reads             ~5.6%
  _tlv_get_addr 2.28 (dyld thread-local address fetch)
  pcc_gc_backend 1.01   _gc_backend_fast 1.08
  frame_roots_disabled_fast 1.20
actual work
  tagged_int_to_str_obj 1.77   memset 2.21   memmove 1.33
```

Every temporary string runs a full **allocate -> index -> refcount -> untrack ->
free** lifecycle, and each stage is a call. `"item" + str(i)` in a loop creates
two such objects per iteration; CPython creates the same objects but its
allocation is a pymalloc pool bump and its untracking is a linked-list unlink,
neither of which crosses a function boundary.

`_tlv_get_addr` at 2.28% is worth naming separately: that is the dynamic
loader's thread-local lookup, paid because runtime state lives in TLS. The
minor-graph lock's comment already records fighting this leaf once.

**This is the value-model problem stated in the project's own north star**
("opt-in value model -- identity-free immutable payloads for hot paths"). A
short-lived string that never escapes does not need an identity, an index entry,
a refcount or a weakref slot; today it gets all four. No amount of shaving the
individual barriers reaches this, because the barriers are already minimal --
measured and documented above.

That is the honest boundary of what per-operation tuning can deliver here, and
it is why the remaining gap on `str`/`dict`/`attr` is a design slice, not a
tuning slice.
