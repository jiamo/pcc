# Investigation: typed-int unboxed arithmetic silently wraps on i64 overflow (violates Python bignum semantics)

## Status
active

## Problem Description
An external intent-vs-reality audit (2026-05-30) flagged, as the single
most-should-fix-now item, a real correctness hole in obligation 2 ("performance
must be proven; when the fast-path assumption fails, the slow path must preserve
Python semantics"):

> typed-int scalar path emits raw `builder.add/sub/mul` (i64 wraparound,
> `binary_op_lowering.py` `_emit_binop_int`), whereas the boxed `py_int.c` path
> overflows to bignum. typed-int accumulation past 2^63 silently diverges from
> CPython, and there is no scalar-overflow negative test.

This is a genuine bug (silent wrong result), not a missing feature: pcc *claims*
Python int semantics but a typed-int (unboxed i64) computation that overflows
returns the i64-wrapped value instead of the arbitrary-precision result CPython
produces.

## Repro
Two-part finding (the precise trigger matters — the obvious case is NOT buggy):

DOES NOT trigger (large literals / accumulator -> pcc correctly boxes):
```python
def main():
    x = 9223372036854775807      # 2^63-1
    x = x + 1
    print(x)                     # pcc=9223372036854775808  CPython=same  (IDENTICAL)
    y = 9223372036854775800
    total = 0
    for i in range(20):
        total = total + y
    print(total)                 # pcc=184467440737095516000  CPython=same (IDENTICAL)
main()
```
IR for this program: a MIX of `add i64` (unboxed, for the bounded loop counter)
and `call @py_int_add` (boxed bignum, for the large-literal arithmetic). The
type inference routes large/growing values to the boxed path -> correct.

DOES trigger (explicit `int`-typed function params -> unboxed i64 -> wraps):
```python
def mul(a: int, b: int) -> int:
    return a * b
def addf(a: int, b: int) -> int:
    return a + b
def main():
    big = 1099511627776          # 2^40
    print(mul(big, big))         # pcc=0                       CPython=1208925819614629174706176   WRONG
    print(addf(9223372036854775807, 5))  # pcc=-9223372036854775804  CPython=9223372036854775812  WRONG
    n = 3037000500
    print(mul(n, n))             # pcc=-9223372036709301616    CPython=9223372037000250000          WRONG
main()
```
Command (DEFAULT no-libpython mode):
```
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/ovf2.py -o /tmp/ovf2_bin
/tmp/ovf2_bin            # prints the wrapped (wrong) values
python3 /tmp/ovf2.py     # prints the correct bignum values
```
IR for the explicit-param program: `mul i64` + `add i64` (unboxed, wraps) for the
typed-param arithmetic. So the trigger is **explicit `a: int` params** (the type
system pins them to unboxed i64), NOT large literals.

## Test [CONFIRMED]
The two repros above were run 2026-05-30: the large-literal/accumulator case is
diff-IDENTICAL to CPython; the explicit-int-param case DIVERGES (silent i64
wraparound). Confirmed at both the code level (`binary_op_lowering.py`
`_emit_binop_int` emits raw `builder.add/sub/mul`) and the observable level (the
diff above). A focused regression must assert the explicit-int-param overflow
case matches CPython (currently it does not) — to be added with the fix.

## Proposals
- No.1 overflow-checked fast path + boxed promotion   [pending — DESIGN-SENSITIVE]
- No.2 conservative unboxing (don't unbox values that can overflow)  [pending]

## No.1 overflow-checked fast path + boxed promotion
### Code Change (described, NOT yet applied)
In `_emit_binop_int` (`binary_op_lowering.py`), replace `builder.add/sub/mul`
with `llvm.sadd/ssub/smul.with.overflow`; on the overflow bit, branch to a slow
path that recomputes via the boxed `py_int_add/sub/mul` (bignum-capable) and
yields a BOXED result.
### DESIGN TENSION (why the audit's "fix isn't big" is optimistic)
The unboxed typed-int result is consumed downstream **as i64**. A bignum does not
fit i64, so on overflow the result's *representation* must change (i64 ->
boxed/tagged). That is exactly the unboxed-speed-vs-arbitrary-precision tension:
- unboxed i64 (current fast path) is fast but CANNOT represent the overflow
  result;
- to stay correct, an overflowing op must produce a tagged-int / boxed value, so
  the downstream (the `int`-typed slot, the function return ABI) must accept
  either i64 or boxed — i.e., typed-int results would need to be tagged ints
  (PY_IS_TAGGED_INT) that promote to bignum, which is essentially the boxed path
  and erodes the unboxed-i64 perf benefit.
So the correct fix is a careful representation/ABI design decision (shared
codegen, bootstrap-critical, broad blast radius), not a localized patch. Do NOT
rush it at the tail of an unrelated session (AGENTS.md Debugging Playbook §9: no
broad speculative edits in shared codegen; feedback_test_first: full bootstrap
before claiming).

## No.2 conservative unboxing
### Code Change (described)
Keep unboxed i64 only where overflow provably cannot occur (e.g. bounded loop
counters, range indices); box `int`-typed function params / arbitrary-provenance
ints so their arithmetic goes through the bignum-capable `py_int_*` path.
### Trade-off
Correct + simpler than No.1, but erodes typed-int's perf benefit for the common
`def f(a: int, b: int)` case (the whole point of obligation 7's value-model
bridge is fast int arithmetic). Likely the wrong long-term answer but a safe
stopgap; measure the perf delta before choosing.

## Recommended design direction (for the focused fix session)
Perf is NOT a reason to keep the bug: CPython ints are *always* overflow-checked
(it is inherent to arbitrary precision), so a correct typed-int with an
overflow-checked fast path is still faster than CPython (no boxing for the common
i63-fitting case, just a predicted-not-taken overflow branch). The right design
is the **tagged-int with an unboxed fast lane**: typed-int arithmetic yields a
tagged int (i63 value fits inline; overflow promotes to a boxed bignum), and the
raw-i64 fast lane is used only for sub-expressions proven to stay in range. This
is Proposal No.1 done honestly: `llvm.sadd/ssub/smul.with.overflow` fast path +
`py_int_*` boxed slow path, with the RESULT REPRESENTATION being tagged-int (not
raw i64) so the overflow result can be carried. Expect to touch `_emit_binop_int`,
the typed-int result consumers (return ABI, slot store), and the IR-shape gates
that currently assert "no py_int_* in the typed-int loop" (those gates must be
relaxed to allow the overflow slow path, or scoped to the fast path). Land behind
the minimized repro + the xfail test below + a full bootstrap gate.

## Test tracking
`tests/python/test_native_typed_int_overflow.py` (added 2026-05-30) is an
xfail(strict=False) run-based regression asserting the explicit-int-param
overflow case matches CPython's bignum output. It XFAILs today (captures the
bug); when the fix lands it flips to xpass — remove the marker then.

## Notes
This is the highest-priority CORRECTNESS item from the 2026-05-30 audit (it
violates obligation 2's slow-path-preserves-semantics red line). It is distinct
from the no-libpython idiom-coverage work (B-P0-PKG support) that filled the
rest of that session — those were validated additive runtime methods; this is a
pre-existing scalar-arithmetic semantics hole in shared codegen. Pick a proposal,
prototype behind a minimized repro, and gate with the full bootstrap before
landing.

## Update 2026-05-31 — user direction (type semantics rule), refined bug surface, Phase 0 tests

### Type semantics rule (Phase 1 — now the binding contract)
The user pinned the design direction. Record it as the contract so the
"is typed int an i64?" confusion does not recur:

> Python annotation `int` means Python arbitrary-precision int (bignum).
> A raw machine integer requires an explicit pcc-owned type (e.g. `pcc.i64`).
> Unboxed i64 is an OPTIMIZATION, never the user-visible meaning of `int`.

So raw-i64 lowering is only legal for a value PROVEN in range (bounded loop
counter / range index / proven-bounded subexpression), never as the default for
an `int`-typed value. Priority ordering (user): **P0 correctness > performance >
package expansion** — silent-wrong `def f(a:int,b:int): return a*b` punches a
hole in strict-native credibility, so it outranks the package fallback-shrink
work (#71-#73).

### Chosen fix (user decision)
- IMMEDIATE = conservative boxing (Proposal No.2 done right): default Python
  `IntType` arithmetic to the boxed `py_int_*` path; keep raw i64 ONLY for
  contexts already proven safe (bounded loop/range/index). Correct, simpler,
  accept the perf cost on the `def f(a:int)` path for now.
- LONG-TERM = tagged-int fast lane (Proposal No.1): `llvm.sadd/ssub/smul`
  `.with.overflow` fast path + bignum promotion, result represented as
  tagged-int so the overflow result is carried through slots / return ABI.

### Refined bug surface (probed 2026-05-31, explicit `a:int` params)
Not every op is wrong — the conservative fix only needs to close the wrong ones:
- WRONG (raw i64, must fix): `+`, `*`; a `*` result carried into a chained `+`;
  the overflow value carried through a function RETURN ABI; through a LOCAL
  SLOT; and `<<` (raw i64 shl masks the count — `1<<100` gave `1<<36`).
- ALREADY CORRECT (already boxes): `-` (subtraction); `a*b > <large literal>`
  comparison.
So the locus is the `+`/`*`/`<<` path through `_emit_binop_value` ->
`_emit_binop_int` (binary_op_lowering.py:711-721), plus making the boxed result
survive the slot-store and return-ABI representation.

### Root-cause locus (source-confirmed)
`binary_op_lowering.py::_emit_binop_value` ends (line ~711) with the
unconditional "Integer path": `lv = _to_int64(lhs); rv = _to_int64(rhs); return
_emit_binop_int(op, lv, rv)`. `_emit_binop_int` (715) emits raw `builder.add/
sub/mul`. The boxed, bignum-capable path `_emit_runtime_int_binop_value` (752,
`py_int_add/sub/mul/...`) already exists — the fix routes Python-`int` `+`/`*`/
`<<` there unless the operands are proven raw-i64-safe.

### Phase 0 tests added (acceptance criteria for the fix)
`tests/python/test_native_typed_int_overflow.py` now has 5 xfail(strict=False)
cases: the original `+`/`*` param overflow, plus chained `a*b+c`, overflow
through the return ABI, through a local slot, and `<<` promotion. All 5 XFAIL
today (capture the bug); they flip to xpass when the fix lands -> remove the
markers then.

### Proposal verdicts
- No.2 (conservative boxing) — SELECTED as the immediate fix (user direction).
- No.1 (tagged-int fast lane) — deferred long-term follow-up.

### Implementation plan (Phase 2, next focused session — NOT rushed at a tail)
1. Introduce a naming/semantics distinction so raw i64 cannot be the accidental
   default: rename/wrap `_emit_binop_int` as the UNCHECKED i64 path
   (`_emit_binop_i64_unchecked`), add a `_int_op_can_use_raw_i64(...)` gate.
2. In `_emit_binop_value`, route Python `IntType` `+`/`*`/`<<` to the boxed
   `_emit_runtime_int_binop_value` UNLESS `_int_op_can_use_raw_i64` proves the
   operands bounded (start conservative: only the existing proven-safe
   loop/range/index contexts stay raw i64).
3. Ensure the boxed result survives the LOCAL-SLOT store and the function
   RETURN ABI (the chained/return/slot xfail cases).
4. Relax/scope the IR-shape gates that assert "no `py_int_*` in the typed-int
   loop" to the proven-safe fast lane only.
5. Gate: the 5 xfail cases must xpass (run with `--runxfail` during dev), the
   Python semantic-alignment focused tests, the fallback baselines, and the
   FULL stage1->stage2->stage3 self bootstrap (local repro is necessary but not
   sufficient). Record the measured perf delta in this doc.

## Update 2026-05-31 (b) — Phase 2 feasibility finding: No.2 (conservative boxing) inverts the obligation-7 unboxed gate

Before touching `_emit_binop_value`, investigated the blast radius. Two facts:

1. **Loop INDUCTION counters are NOT routed through `_emit_binop_value`** — the
   for/range fast path (`for_loop_lowering.py` inline induction, ~line 955)
   keeps its counter in a raw entry-block i64. So routing `_emit_binop_value`'s
   int path to boxed does NOT touch `for i in range(...)` counters. Good.

2. **But `test_py_typed_int_unboxed.py` (14 passed today) is a live obligation-7
   gate that the conservative default-boxed fix WOULD break/invert.** Its programs
   are `while i < n: total = total + step(i); i = i + 1` and list-sum
   accumulators — explicit `int` locals whose `+`/`*` go through
   `_emit_binop_value`. The gate asserts they stay UNBOXED (`@py_int_add not in`,
   `define i64 @user_*_bench(i64 %n)`, `call i64 @user_*_step(i64 %)`).
   - A while-loop COUNTER (`i = i + 1`, `i < n`) is provably bounded -> a smart
     safe-set could keep it unboxed.
   - But an ACCUMULATOR (`total = total + step(i)`) is fundamentally UNboundable
     -> under the "int = bignum unless proven-safe" rule it MUST box. So No.2
     boxes `total`, the gate's `@py_int_add not in` assertion FAILS, and the gate
     would have to be INVERTED (assert boxed) — i.e. No.2 effectively REMOVES the
     typed-int unboxed fast path for accumulator loops, gutting obligation 7's
     value-model perf bridge, not merely "accepting some perf cost".

### Revised recommendation (cost data changes the No.1-vs-No.2 calculus)
No.2's real cost is "remove the typed-int unboxed fast lane for non-range loops +
invert 14 obligation-7 gate assertions", which is larger than the "accept some
perf cost" framing. **No.1 (tagged-int / overflow-checked fast lane) now looks
like the better IMMEDIATE choice, not just long-term:** it KEEPS the unboxed
`add i64`/`mul i64` fast path and adds a not-taken `sadd/smul.with.overflow`
branch that promotes to boxed bignum only on actual overflow. The obligation-7
gates then change SHAPE (unboxed-add + overflow-branch) rather than invert to
fully boxed — preserving the perf bridge for the common i63-fitting case while
being correct. The cost is implementation: the typed-int result representation
must be tagged-int (i63 inline / boxed on overflow) so the overflow result
survives the local-slot store and the function return ABI. This is genuinely
shared-codegen + bootstrap-critical work.

### Status of Phase 2
Surfaced to the user (the No.1-vs-No.2 tradeoff is sharper than at the original
decision: No.2 guts the unboxed fast lane; No.1 preserves it but needs the
tagged-int rep). NOT implementing the broad `_emit_binop_value` change until the
direction is re-confirmed with this cost data — per "do not patch
binary_op_lowering speculatively" and AGENTS.md §9. Phase 0 (5 xfail) + Phase 1
(semantics rule) remain landed and correct regardless of which fix is chosen.

## Update 2026-05-31 (c) — Phase 2 EMPIRICAL finding: localized analysis tweaks do NOT fix it; the fix is a rep/ABI change (confirmed)

Tested two localized hypotheses at the typed-int analysis layer; BOTH had ZERO
effect on the bug (mul(2**40,2**40) still printed 0), then reverted (AGENTS.md
§9 — no speculative edits that don't fix the target):

1. Tightened `_typed_int_expr_is_i64_safe` (typed_int_abi.py:925) to drop
   `*`/`<<` from the safe-preserving BinOp set. NO effect — this predicate
   feeds assignment-target / value-safe flags, not the function param/return
   ABI for `def mul(a:int,b:int)`.
2. Excluded `*` from `_expr_is_native_typed_int_shape` (the per-function
   "eligible for native int ABI" predicate, typed_int_abi.py:120). NO effect
   either.

IR confirms why: `define external i64 @user_*_mul(i64 %a, i64 %b)` with a raw
`mul i64`. The i64 param/return ABI comes from the unconditional
`_type_is_typed_int_abi_param(IntType) == True` (typed_int_abi.py:95) — an
`int` annotation maps to an i64 param REGARDLESS of the body-shape predicates I
edited — and the arithmetic falls to `_emit_binop_int`'s raw `mul i64`
(binary_op_lowering.py:721). To return a bignum, `mul`'s signature must change
from `i64` to a PyObject* (tagged/boxed) — i.e. the REPRESENTATION/ABI must
change. There is no localized-analysis fix: a correct bignum result cannot flow
through an `i64` return.

### Confirmed conclusion (empirical, not just the No.1 DESIGN TENSION prose)
The bignum-correct fix is a **typed-int representation/ABI change** (`int`
param/return/slot: i64 -> tagged PyObject*), touching: the IntType->i64 param
mapping (`_type_is_typed_int_abi_param`), the arithmetic (`_emit_binop_int`),
the call ABI (callers pass i64), AND inverting/relaxing the ~14 obligation-7
unboxed gates (`test_py_typed_int_unboxed.py` asserts `define i64 @...(i64)` +
`@py_int_add not in`). This is a dedicated SUBPROJECT, not a /loop slice, and
not a localized patch — empirically confirmed by the two no-effect experiments
above. It validates Proposal No.1's DESIGN TENSION at the code level.

### Landed + durable regardless of when the subproject runs
- Phase 0: `tests/python/test_native_typed_int_overflow.py` — 5 xfail cases
  capturing the +/* param overflow + chained + return-ABI + local-slot + <<
  (the fix's acceptance criteria).
- Phase 1: the type-semantics rule (int = bignum; raw i64 = optimization /
  explicit pcc.i64).
- Refined surface: `-` and `a*b > literal` already box; `+`/`*`/`<<` wrap.

### Recommendation
Run this as a focused multi-iteration SUBPROJECT (tagged-int rep: i63 inline /
boxed on overflow, so the unboxed fast lane survives for the common case and the
gates change SHAPE rather than invert), each step gated by the full
stage1->stage2->stage3 bootstrap. It is the #1 audit correctness item but is NOT
completable as a single /loop iteration. Until it is scheduled, the bug remains
documented + captured by the 5 xfail tests.

## Update 2026-05-31 (d) — conceptual frame: this fix IS Python int's value/object projection (Valhalla projection model, NOT Java int)

The fix is grounded in the value model's projection principle (docs/goal/goal-prompt.md
V-track "Projection model"): `int` is a SEMANTIC type (arbitrary precision,
always) with two projections — a VALUE projection (tagged small-int inline fast
lane) and an OBJECT projection (boxed bignum). Overflow of the value projection
must DEOPT/PROMOTE to the object projection; it may NOT wrap.

The current bug is precisely the confusion the projection model forbids: the
implementation equates the Python *semantic* type `int` with a *machine
representation* `i64` (IntType -> `_type_is_typed_int_abi_param` -> i64 param +
`_emit_binop_int` raw `mul i64`), so the value projection wraps with no promotion
to the object projection. That is borrowing Java's fixed-width `int` overflow —
exactly what pcc must NOT do.

Correct design (three semantic types, not one representation):
- `int` -> tagged value lane + boxed bignum object lane (this fix);
- `pcc.i64` / `pcc.u64` -> the explicit machine-integer type, where wrap / trap /
  checked / saturating is a written-in-the-type choice (the ONLY place
  fixed-width overflow semantics are legal);
- `@pcc.valueclass` fields -> semantics follow the FIELD type (`int` field =
  Python bigint via tagged lane; `pcc.i64` field = raw machine).

So the rep/ABI change this investigation concluded is needed (i64 -> tagged
PyObject* for `int`) is not an ad-hoc patch — it is implementing `int`'s
value/object projection, which the value model already requires. The
tagged-int-with-overflow-fast-lane (Proposal No.1) is the value projection;
`py_int_*` bignum is the object projection; the seam between them is explicit and
overflow-promoting. (The eventual `pcc.i64` explicit machine type is the separate
home for raw-i64 semantics — a V-track type-system addition, not part of closing
this bug, but the reason `int` must stop meaning i64.)

## Update 2026-06-11 — DESIGN REVERSAL: the tagged value projection ALREADY EXISTS; the bug is only the unboxed-ABI admission predicate

Source-level audit (2026-06-11, full chain read) found that the "dedicated
subproject / viral ABI change" framing from Update (c) is obsolete. The
repository already contains the correct representation; the bug is an
over-permissive admission gate into an opt-in raw-i64 lane.

### Facts (source-confirmed, with anchors)

1. **Runtime tagged ints already exist** (`pcc/py_runtime/src/py_internal.h:221-236`):
   `PY_IS_TAGGED_INT(p) = ((uintptr_t)p & 1) == 1` (LSB=1 = small int — the
   GC-safe OCaml polarity; real pointers stay LSB=0), payload `bits >> 1`
   (i63: `PY_TAGGED_INT_MIN = INT64_MIN >> 1`). `py_int_core.c` collapses
   small bignum results back to tagged; `py_type_tag_of` handles tagged words
   (`py_internal.h:795`). So GC discrimination of scalar-vs-pointer slot words
   is ALREADY solved for all five backends — tagged ints flow through
   PyObject* slots today.
2. **Codegen already emits an inline tagged fast path** for `+ - & | ^` on
   boxed operands: `_emit_inline_tagged_int_binop_or_call`
   (`binary_op_lowering.py:912-1071`): both-LSB-tagged check -> `ashr 1`
   untag -> raw op -> for `+`/`-` a range check against `[-2^62, 2^62-1]` ->
   retag `(v<<1)|1` -> phi join with the `py_int_*` slow call. No heap
   allocation on the fast path. `*` is MISSING from the op gate (line 919) —
   boxed `*` always calls `py_int_mul`.
3. **The boxed/tagged world is the DEFAULT.** `_should_box_python_ints()`
   == `not _module_uses_raw_int_scaffold`, and that flag is only ever set
   False (`layer1_init.py:83`) — so module code defaults to boxed ints with
   `_box_int_locals=True` per function (`type_abi_lowering.py:136-165`,
   `user_function_lowering.py:1164`). The boxed default is bignum-correct
   end-to-end (params, returns, slots, prints).
4. **The unboxed i64 lane is opt-in per function** via
   `_funcdef_uses_unboxed_typed_int_abi` (`typed_int_abi.py:269-342`):
   enabled by `PCC_PYTHON_TYPED_INT_ABI` (default `auto`=on; `off|boxed|0|
   false`=off — a kill switch already exists, `typed_int_abi.py:68-78`);
   admission = not async/method/decorated, return in (IntType, FloatType),
   all params in (Int|Bool|Float|List[int]), plus a call-arg safety pre-pass.
   Note the admission does NOT consult `_expr_is_native_typed_int_shape` /
   `_stmt_block_is_native_typed_int_shape` — which is exactly why Update (c)
   experiment 2 (excluding `*` from the body-shape predicate) had no effect.
5. **The call-arg safety pre-pass checks the WRONG property.**
   `_typed_int_expr_is_i64_safe` (`typed_int_abi.py:900-934`) proves "the
   argument VALUE is representable in i64" (literal fits, safe name, closed
   under `+ - * // % & | ^ << >>`), not "the callee's COMPUTATION cannot
   leave i64". `mul(2**40, 2**40)`: both literals fit i64 -> args "safe" ->
   function admitted to the raw-i64 lane -> `mul i64` wraps. The product
   2^80 was never representable; the analysis never asks that question.

### Revised design (supersedes the "subproject" framing; cost collapsed)

"Demote to the boxed ABI" IS the i64->tagged-PyObject* representation change,
done with machinery that already exists and is already the default. Plan:

- **Slice 1 (correctness, small):** restrict `_funcdef_uses_unboxed_typed_int_abi`
  admission to float-only signatures (FloatType return AND all-FloatType
  params; Python float IS a machine double, no overflow-to-bignum exists).
  Int/Bool/List[int] signatures take the default boxed/tagged ABI — the
  existing bignum-correct value projection. Expected effect: all 5 xfail
  cases in `tests/python/test_native_typed_int_overflow.py` flip to xpass
  (remove markers). Optional: accept `PCC_PYTHON_TYPED_INT_ABI=unsafe-i64`
  as an explicitly-labeled legacy/diagnostic escape that re-admits int
  signatures (mode-labeled, never default). Keep the predicate body in the
  same primitive direct-field style (the function's own comment records a
  pcc1 miscompile of a fancier Optional-sentinel version).
- **Slice 2 (perf mitigation, contained):** add `*` to
  `_emit_inline_tagged_int_binop_or_call`: untag both -> `llvm.smul.with.
  overflow.i64` -> overflow-bit OR out-of-tagged-range -> slow `py_int_mul`;
  else retag. The self backend already implements this intrinsic
  (`self_backend_aarch64_darwin_calls.py:1506` ->
  `emit_smul_overflow_intrinsic_call`); the C frontend already emits it
  elsewhere, so llvmlite/llvm_capi handle it. NOTE: the x86_64 Linux self
  backend has NO `with.overflow` emitters yet — Darwin-arm64-only claim.
- **Slice 3 (gates change SHAPE, not invert):** rewrite the int-function
  assertions in `tests/python/test_py_typed_int_unboxed.py` (the ~14
  obligation-7 gates) to the tagged-shape contract: boxed ABI signature +
  inline `tag.fast`/`tag.add` blocks present + no `py_instance_new` /
  no allocation on the fast path + slow-path `py_int_*` allowed. Float
  gates stay as-is (float lane keeps the unboxed double ABI). Add a gate
  asserting the for/range induction fast path (separate lane,
  `for_loop_lowering.py` inline induction) still emits raw i64 — bounded
  counters must not regress.
- **Risks / measurements:** bootstrap compiles the compiler's own int-typed
  helpers — demotion costs perf there; measure stage walls vs the current
  off-mode baseline (5.9/12.8/13.0s) and record the delta here. Mandatory
  full self bootstrap gate before claiming (AGENTS.md); fallback baselines
  should be unaffected (no new fallbacks — all-native paths).
- **Future (T2, separate):** a real interval/range analysis may re-admit
  proven-bounded int signatures to the raw-i64 lane; the explicit `pcc.i64`
  machine type (V-track) is the only legal home for wrap semantics.

### External prior-art research
A deep-research run on tagged-int designs (OCaml i63 / V8 Smi / mypyc tagged
native int / SBCL fixnum->bignum promotion / CPython tagged-pointer plans,
overflow-check codegen cost, AOT promotion strategies, check elision) was
launched as workflow run `wf_06db2ba2-b06` (2026-06-11, this session); attach
its synthesized report here when it completes. The local design above stands
on source facts alone; the research is corroboration + cost numbers.

### Status of this update
Design ready for implementation (Slices 1-3). NO code changed yet in this
update; the 5 xfail tests still capture the bug. Next session: implement
Slice 1 behind the focused overflow tests, then Slice 2/3, then full
bootstrap + record perf deltas.

## Update 2026-06-11 — Slice 1 landed: default int annotations use boxed/tagged ABI

Slice 1 is implemented and gated. The raw-i64 typed-int function ABI is no
longer the default path for Python `int` annotations.

### Code Change

- `pcc/py_frontend/codegen/typed_int_abi.py` now parses
  `PCC_PYTHON_TYPED_INT_ABI` into explicit modes: `off`, `auto`, and
  `unsafe-i64`.
- In default `auto` mode, `_funcdef_uses_unboxed_typed_int_abi(...)` admits
  only float-only signatures: `FloatType` return plus all `FloatType`
  parameters. Python `int`, `bool`, and `list[int]` signatures fall back to
  the existing boxed/tagged Python-int ABI.
- `unsafe-i64` preserves the old raw Int/Bool/Float/List[int] ABI as an
  explicitly mode-labeled legacy/diagnostic escape. The old call-argument
  i64-safety pre-pass is now consulted only in this unsafe mode, because it
  proves only argument representability, not result representability.
- `pcc/py_frontend/pipeline.py` mirrors the same mode and signature rules for
  closed-world export metadata, avoiding cross-module ABI drift.
- `tests/python/test_native_typed_int_overflow.py` no longer has xfail markers;
  the five overflow cases are ordinary regression tests.
- `tests/python/test_py_typed_int_unboxed.py` now has a default ABI guard that
  `def add(a: int, b: int) -> int` lowers to a `ptr(ptr, ptr)` boxed/tagged
  ABI with `py_int_add`, a default pure-float unboxed guard, and explicit
  `PCC_PYTHON_TYPED_INT_ABI=unsafe-i64` labels for the legacy raw-i64 gates.

### Evidence

Focused red-before-green:

- Before the patch,
  `env -u LC_ALL uv run pytest tests/python/test_native_typed_int_overflow.py --runxfail -q -n0`
  failed 5/5, with wrapped outputs such as `0`, `-9223372036854775804`,
  `7`, `1`, and `68719476736`.
- After the patch, the same `--runxfail` command passed: 5 passed in 36.01s.
- After removing xfail markers,
  `tests/python/test_native_typed_int_overflow.py` passed: 5 passed in 4.53s.
- Typed-int shape gate:
  `tests/python/test_py_typed_int_unboxed.py` passed: 16 passed in 4.84s.
- Focused typed-int batch:
  `tests/python/test_native_typed_int_overflow.py tests/python/test_py_typed_int_unboxed.py`
  passed: 21 passed in 8.41s.

Broader touched-path gates:

- Multi-file frontend/bootstrap shim:
  `tests/python/test_py_multi_file_compile.py tests/python/test_py_multi_file_bootstrap_shim.py`
  passed: 106 passed in 205.09s.
- LLVM-C API parity/end-to-end batch:
  `tests/c/test_llvm_capi_ir_parity.py tests/c/test_llvm_capi_end_to_end.py`
  passed: 24 passed in 0.24s.
- Fallback/no-libpython baselines:
  `tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py`
  passed: 18 passed in 117.06s.
- Self-host oracle diff:
  `tests/python/test_self_host_oracle_diff.py` passed: 397 passed in 635.47s.
- Bootstrap gate baseline:
  `tests/python/test_bootstrap_gate_baseline.py` skipped in this environment:
  4 skipped in 0.19s.
- Full five-GC three-stage bootstrap matrix:
  `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py -q -n0 -s`
  passed: 5 passed in 413.86s; each backend reported pcc2/pcc3
  byte-identical after signature normalization.
- Direct off-mode three-stage bootstrap with profile output
  `/tmp/pcc_int_slice1_boot.g62kbi/profile` passed and verified pcc2/pcc3
  signature-normalized byte identity. Stage wall / compile wall:
  stage1 15.2s / 14.1s, stage2 19.9s / 18.9s, stage3 21.2s / 19.9s.
  Compared with the earlier prompt baseline 5.9s / 12.8s / 13.0s, this run
  is +9.3s / +7.1s / +8.2s wall. This is recorded as an environment/run
  measurement, not a performance completion claim.
- Full GC production contract:
  `tests/python/gc_production_contract` passed: 130 passed in 26.71s.
- Hygiene passed: touched-file `py_compile`, `git diff --check`, and touched
  docs/code trailing-whitespace check.

### Status

Slice 1 is `DONE_WEAK`: it closes the default Python-`int` silent-wrap
correctness hole by routing annotated `int` functions through the existing
boxed/tagged ABI. This does not claim Slice 2 tagged `*` fast-path support,
Slice 3 obligation-gate shape rewrite, interval/range re-admission,
`pcc.i64`, complete typed-int projection repair, full value-model completion,
full GC research completion, or total-goal completion.

## Update 2026-06-11 — Slice 2 landed: tagged multiplication fast path

Slice 2 is implemented and gated. Boxed/tagged Python-int multiplication now
has the same zero-allocation fast-lane shape as the existing tagged `+` / `-`
paths, while keeping Python bignum semantics through the slow path.

### Code Change

- `pcc/py_frontend/codegen/binary_op_lowering.py` now admits `*` in
  `_emit_inline_tagged_int_binop_or_call(...)`.
- For both-tagged operands, the fast block untags both payloads, calls
  `llvm.smul.with.overflow.i64`, and extracts the `{i64, i1}` result.
- The result is retagged only when the intrinsic overflow bit is false and the
  raw product is inside the tagged i63 range `[-2^62, 2^62 - 1]`.
- Overflow or out-of-tagged-range products branch to the existing
  `py_int_mul` slow path, so bignum results such as `2**40 * 2**40` remain
  correct.
- The overflow-pair extraction uses `[0]` / `[1]` field paths, not bare `0` /
  `1`, because full self-host oracle testing exposed that pcc1's IR scaffold
  supports the list-path shape used elsewhere by valueclass payload lowering.
- `tests/python/test_py_typed_int_unboxed.py` adds a focused IR-shape guard
  and a strict self-backend/no-libpython runtime guard for small tagged
  products, tagged-range overflow to bignum, and signed-i64 overflow to bignum.

### Evidence

Focused red-before-green:

- Before the patch,
  `tests/python/test_py_typed_int_unboxed.py::test_tagged_int_mul_uses_inline_overflow_fast_path -q -n0`
  failed because boxed/tagged `def mul(a: int, b: int) -> int` went straight
  to `py_int_mul` with no `int.tag.fast` block and no
  `llvm.smul.with.overflow.i64`.
- After the first implementation, full `tests/python/test_self_host_oracle_diff.py`
  exposed two pcc1 compile failures (`ternary_value` and `typed_args_return`).
  Focused oracle repros passed after changing the new
  `builder.extract_value(pair, 0/1, ...)` calls to
  `builder.extract_value(pair, [0]/[1], ...)`.

Final Slice 2 gates:

- New focused IR/runtime tests:
  `tests/python/test_py_typed_int_unboxed.py::test_tagged_int_mul_uses_inline_overflow_fast_path`
  and
  `tests/python/test_py_typed_int_unboxed.py::test_tagged_int_mul_fast_path_runs_without_libpython`
  passed: 2 passed in 32.55s.
- Focused oracle repro after the scaffold fix:
  `tests/python/test_self_host_oracle_diff.py -q -n0 -k 'ternary_value or typed_args_return'`
  passed: 2 passed, 395 deselected in 13.52s.
- Full typed-int shape/runtime file:
  `tests/python/test_py_typed_int_unboxed.py` passed: 18 passed in 5.74s.
- Focused typed-int batch:
  `tests/python/test_native_typed_int_overflow.py tests/python/test_py_typed_int_unboxed.py`
  passed: 23 passed in 10.07s.
- Adjacent int arithmetic / binary protocol / dunder dispatch batch:
  `tests/python/test_python_int_arithmetic_parity.py tests/python/test_py_binary_protocols.py tests/python/test_py_dynamic_dunder_arithmetic.py tests/python/test_py_dynamic_dunder_binary.py tests/python/test_binary_dunder_dispatch_runtime.py`
  passed: 18 passed, 3 skipped in 11.09s.
- Self-backend overflow intrinsic guard:
  `tests/c/test_self_backend.py::test_self_backend_aarch64_call_helpers_cover_umul_overflow_and_assume_intrinsics`
  passed: 1 passed in 0.24s.
- Fallback/no-libpython baselines:
  `tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py`
  passed: 18 passed in 127.37s.
- Self-host oracle diff:
  `tests/python/test_self_host_oracle_diff.py` passed: 397 passed in 647.07s.
- Full five-GC three-stage bootstrap matrix:
  `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py -q -n0 -s`
  passed: 5 passed in 420.55s; GC0, GC1, GC2, GC3, and GC4 each reported
  pcc2/pcc3 byte-identical.
- Touched-file `py_compile` passed for the changed Python code and touched
  Python tests.
- Hygiene passed: `git diff --check`, touched-file trailing-whitespace search,
  and residual `pcc`/`pytest`/`bootstrap.sh` process checks were clean.

### Status

Slice 2 is `DONE_WEAK`: it proves tagged multiplication has an inline
overflow-checked fast path and a Python-semantics-preserving bignum slow path
under the current Darwin-arm64 self-backend gate. This does not claim Slice 3
obligation-gate shape rewrite, interval/range re-admission, `pcc.i64`,
x86_64 Linux self-backend `with.overflow` support, complete typed-int
projection repair, full value-model completion, full GC research completion,
or total-goal completion.

## Update 2026-06-12 — Slice 3 landed: obligation gates assert tagged shape

Slice 3 is implemented and gated. The test suite no longer treats default
annotated Python `int` functions as raw-i64 functions. The old raw-i64 gates
remain only as explicitly mode-labeled `unsafe-i64` diagnostics.

### Code Change

- `tests/python/test_py_typed_int_unboxed.py` now asserts the default
  tagged-shape contract for int loops, list-int loops, direct calls, and
  function-call loops:
  boxed `ptr` function ABI, `int.tag.fast` / `tag.add` fast blocks, retained
  `py_int_*` slow paths, no CPython fallback, and no class/valuebox allocation
  in the checked bodies.
- The old raw-i64 tests were renamed to `unsafe_i64_*` and explicitly set
  `PCC_PYTHON_TYPED_INT_ABI=unsafe-i64`, making that lane diagnostic/legacy
  instead of the default Python-`int` claim.
- The pure-float gate remains a default unboxed double ABI assertion.
- A new `for range(n)` induction gate proves the bounded range lane still uses
  raw `i64` phi/icmp/add under boxed-int mode, while boxing the loop variable
  back to Python `int` for body semantics.

### Evidence

- Full typed-int shape/runtime file:
  `tests/python/test_py_typed_int_unboxed.py` passed: 23 passed in 7.09s.
- Focused typed-int batch:
  `tests/python/test_native_typed_int_overflow.py tests/python/test_py_typed_int_unboxed.py`
  passed: 28 passed in 10.51s.
- Adjacent int arithmetic / binary protocol / dunder dispatch batch:
  `tests/python/test_python_int_arithmetic_parity.py tests/python/test_py_binary_protocols.py tests/python/test_py_dynamic_dunder_arithmetic.py tests/python/test_py_dynamic_dunder_binary.py tests/python/test_binary_dunder_dispatch_runtime.py`
  passed: 21 passed in 11.23s.
- Fallback/no-libpython baselines:
  `tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py`
  passed: 18 passed in 122.76s.
- Self-host oracle diff:
  `tests/python/test_self_host_oracle_diff.py` passed: 397 passed in 798.96s.
- Full five-GC three-stage bootstrap matrix:
  `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py -q -n0 -s`
  passed: 5 passed in 434.14s; GC0, GC1, GC2, GC3, and GC4 each reported
  pcc2/pcc3 byte-identical.
- Full GC production contract:
  `tests/python/gc_production_contract` passed: 130 passed in 27.79s.
- Touched-file `py_compile` passed for the changed Python test and relevant
  typed-int/frontend Python files.
- Hygiene passed: `git diff --check`, touched-file trailing-whitespace search,
  and residual `pcc`/`pytest`/`bootstrap.sh` process checks were clean.

### Status

`INT-P0-PROJ` is `DONE_WEAK` as a typed-int silent-wrap/value-projection slice:
default Python `int` no longer silently wraps through the raw-i64 typed-int
ABI, tagged multiplication has an overflow-checked fast path, and the tests now
assert the tagged-shape contract. This does not claim complete typed-int
projection repair, interval/range re-admission, `pcc.i64`, x86_64 Linux
self-backend `with.overflow` support, full value-model completion, full GC
research completion, package ecosystem readiness, or total-goal completion.
