# Investigation: pcc1 exact compiler-arena field projection loses the append4 method ABI

## Status

resolved

## Problem Description

The compiler native-data-plane profile attributes 943 on-CPU samples in the
representative worker to the generic
`IndexedFunctionSeed.append_parsed_call_method_native_adapter`.  Keeping the
parser receiver typed as `IndexedCallPlane` is correct but pays Python method
binding.  Making the receiver exact should permit the ordinary closed-world
native method ABI, but two source-frozen compilers built from that shape fail
before emitting even a 114-byte, no-call LLVM function:

```text
[self.emit] prepare begin bytes=114
self backend emit worker failed: append4
```

This is not the already-resolved valueclass-return export bug in
[`cross-module-valueclass-aggregate-abi-export.md`](cross-module-valueclass-aggregate-abi-export.md).
That investigation proves aggregate method returns.  Here an exact ordinary
class receiver reaches one of its imported `CompilerIntArena` fields with the
wrong method/field projection inside the full compiler closure.

Four reductions are green and constrain the bug: a two-module exact annotated
field, inherited classes with 27 slots, a three-module transitive field
annotation, and a three-module raw `pcc.unsafe` arena all compile and run with
output `4`.  Do not patch generic cross-module fields from the symptom alone;
the failing interaction still requires the real compiler closure/import graph.

## Update — 2026-08-29 minimized field ABI and override reds

The first wrong projection is now localized.  In the real v71 merged IR,
`instruction_record_scalars` is loaded with subclass-local index 16 although
the imported base contributes seven earlier fields, so the runtime index is 23.
A four-module reduction preserves that interaction: `Seed(Base)` declares 14
local scalar fields followed by `arena`, and a consumer with an exact `Seed`
annotation emits `py_instance_get_field(..., i32 14)` instead of inherited-first
index 21.  The checked-in
`tests/python/test_cross_module_inherited_field_abi.py` observed this red before
the export fix and now asserts both index 21 and inherited base index 6.

The required ordinary-Python control exposed a second half of the same ABI
decision.  A cross-module function annotated `value: Base` returned
`Base.ping()` for a `Child` instance that overrides `ping`; the compiled binary
printed `1` while CPython semantics require `2`.  Generated IR showed a direct
`@user_owner_Base_ping` call.  Host-side tracing then proved that the extern
class registry contained `Child` with an empty `bases_ast`, so the subclass
override guard could not discover `Child -> Base`.  This is not evidence that
annotations are exact: ordinary class annotations remain compatible-base
constraints, and direct dispatch requires a closed-world no-override proof.

## Repro

Frozen failing compilers and input:

```bash
gtimeout 30s env -u LC_ALL PCC_DEBUG_SELF_BACKEND_TRACE=1 \
  PCC_GC_BACKEND=0 PCC_PYTHON_IR_PASSES=off PYTHONHASHSEED=0 \
  build/native-data-plane-stage1-candidate-v71-exact-seed-schema/pcc1 \
  --pcc-self-backend-emit-worker /tmp/pcc-seed-min.ll \
  /tmp/pcc-seed-min.v71.result /tmp/pcc-seed-min.v71.s ""
```

Observed exit 1 and the `append4` message above.  Candidate v70 reproduces the
same boundary.  Frozen v67 on the same 114-byte input exits 0 and reaches
`func begin main`.

## Test [CONFIRMED]

The v70 and v71 source-frozen pcc1 binaries both reproduced the failure after
their Stage1 build receipts completed normally.  V71 included exact class-level
types for every `IndexedCallPlane`/`IndexedFunctionSeed` slot, so missing source
annotations alone are disproved.  The four `/tmp/pcc-exact-field-repro`
reductions described above all passed and are controls, not the regression.

At this checkpoint a durable focused test still needed the smallest real
source/module subset that turned one of those controls red, so the v71 source
snapshot plus 114-byte emit command remained the fail-closed gate.  The later
No.3 update records the checked-in reduction and repair.

## Proposals

- No.1 Break the parser dependency cycle and annotate the exact seed receiver [DENIED]
- No.2 Add exact class-level types for every base/seed slot [DENIED]
- No.3 Localize the first wrong field/method projection, then fix its generic frontend owner [CONFIRMED]

## No.1 Break the parser dependency cycle and annotate the exact seed receiver

### Code Change

Move the four pure literal classifiers to a low-level module so
`kernel -> analysis -> literals` no longer cycles through the parser.  Import
`IndexedFunctionSeed` at parser module scope and annotate the hot parser frames
with that exact type.  No arena parameters or record copies were added.

### DENIED

The 426-test focused packet and four pcc1 module closures passed.  Source-frozen
v70 improved Stage1 to 309.409B instructions, but its first 114-byte runtime
canary failed at `prepare begin` with `append4`.  Correctness denies the shape;
no item311 result exists.

## No.2 Add exact class-level types for every base/seed slot

### Code Change

Add class-level annotations matching every inherited/base and seed `__slots__`
field, including all `CompilerIntArena` fields, then repeat No.1 unchanged.

### DENIED

The 427-test packet and four pcc1 closures passed.  Source-frozen v71 Stage1
completed at 309.097B instructions, but the same 114-byte canary failed at the
same `append4` boundary.  The annotations and dependency split were removed;
retained compiler sources hash-match frozen v67.

## No.3 Localize the first wrong field/method projection, then fix its generic frontend owner

### Code Change

After the re-export fixed point, flatten every closed-world class export to the
runtime's inherited-first field order in both serial and parallel frontend
paths.  Preserve the corresponding `field_types` by field name.  When an
extern class is registered early, hydrate its base graph from the same export
table instead of permanently publishing an empty `bases_ast`.  Before direct
instance-style method dispatch, reject the direct path when the closed-world
graph contains a subclass override and use normal runtime attribute binding.

The implementation is generic: it has no compiler-module or arena-name special
case.  The direct path remains available when the receiver class has no
subclass override, including the compiler-owned arena case this task targets.

### CONFIRMED

Focused current-source evidence is green:

```text
2 passed in 3.13s
27 passed in 28.07s
```

The packet covers the minimized inherited-field ABI, ordinary subclass method
override, cross-module valueclass ABI, native annotation dispatch, class symbol
collisions, class-attribute overrides, unbound base calls, cross-module class
inference, large layouts, and inherited dataclass/init ordering.  Strict pcc1
closure/self-emission, the 114-byte canary, and source-frozen performance gates
remained open at that checkpoint; the final update below closes them.

## Update — 2026-08-29 v72 crosses the field boundary; PEP 604 remains dynamic

Source-frozen v72 combines the exact v71 backend source shape with the generic
field/base-graph and override fixes.  It completes Stage1 in 262.35s at
309.331B instructions and 1.651GB peak footprint, links only libSystem, and its
114-byte canary reaches `func begin main` with exit 0.  Two receipt-bound
item311 runs emit exact `ff943e10...` assembly at 401.175B and 401.193B
instructions with a stable 3.111GB footprint.  This is about 4.05% fewer
instructions than v67's 418.176/418.108B and no Stage1 instruction or footprint
regression relative to v67.

That result does **not** yet prove the call adapter is gone.  Disassembly of the
full-closure v72 pcc1 shows `_call_instr_from_parts` loading the
`append_parsed_call` attribute and executing `py_obj_getattr` followed by
`py_obj_call`; the corresponding native adapter symbol is still present.  The
remaining cause is now a checked-in red: `py_lift._lift_type` maps every PEP 604
union to Dyn, so `IndexedFunctionSeed | None` loses its class projection even
though `Optional[IndexedFunctionSeed]` already preserves it.

The next finite slice recognizes only the PEP 604 optional forms `T | None` and
`None | T`, using the same object/primitive rule as existing `Optional[T]`:
object classes/containers keep their projection, nullable unboxed numeric types
and arbitrary unions remain Dyn.  The lift/direct-call/override regression is
green together with 37 annotation/schema tests and strict `py_lift.py`
self/no-libpython closure.  A new source-frozen pcc1 and item311 measurement are
still required; v72 remains the last complete receipt.

## Update — 2026-08-29 v73 exact receiver closes the ABI task

The PEP 604 optional projection is now proven on a source-frozen compiler.
V73 Stage1 completes at 308.470B instructions and links only libSystem; the
114-byte GC0 canary exits 0.  Full-closure machine code for
`_call_instr_from_parts` contains a direct branch to
`IndexedFunctionSeed.append_parsed_call`.  The v72
`py_obj_getattr("append_parsed_call")` + `py_obj_call` sequence is absent from
that exact path.  A compatible `Base | None` receiver with an overriding
`Child.ping` still dispatches dynamically and returns the child result.

Two receipt-bound item311 repetitions are exact `ff943e10...` at 384.116B and
384.247B instructions, 28.38/28.39s CPU and 3.105GB footprint.  This is about
8.1% fewer instructions than retained v67 and about 3% below the pre-migration
v44 envelope.  The repeated instruction spread is about 0.034%.

All required focused, strict closure, bootstrap and fallback gates have final
successful summaries.  Full commands, source/compiler identities, sharded
fallback summaries and claim boundaries are recorded in
[`2026-08-29-exact-compiler-arena-field-abi.md`](../goal/evidence/2026-08-29-exact-compiler-arena-field-abi.md).

## Report

No.3 is the accepted proposal.  The failure was not an arena allocator defect
and did not justify a compiler-specific shortcut: parallel export convergence
published subclass-local field indexes, early extern declarations dropped the
base graph, and PEP 604 optionals erased the exact receiver schema.  The repair
is generic at all three boundaries and preserves ordinary subclass overrides.

No.1 and No.2 remain historically denied on their original source because both
failed the 114-byte canary.  Their backend annotation shape becomes safe only
when combined with No.3's generic ABI repair; those denials are not rewritten
into claims that the earlier binaries were correct.  Stage2/Stage3, fixed point
and GC1..4 remain parent-task work rather than claims of this investigation.

## Update — 2026-08-29 raw-int class-constructor argument projection

The resolved inherited-field repair did not cover a later expression boundary.
In a `pcc.*` raw-int module, a valueclass aggregate local was projected
correctly for comparisons, membership and formatting, but
`Record(value.second, ..., value.third)` routed those constructor arguments
through dynamic `py_obj_getattr`. The self verifier rejected the aggregate as
the callee's expected `ptr` receiver. A compact cross-module test reproduces
the exact failure only when the consumer uses the production raw-int module
policy.

`_classgen_emit_dynamic_attr_value` now consults the named local's recorded
storage IR type. A non-pointer aggregate uses ordinary expression lowering
(`extractvalue`); pointer/object storage retains Python dynamic attribute
semantics. The reduction, existing valueclass ABI tests and dataclass/classgen
packet pass. A source-frozen V85 completed Stage1 and its 114-byte canary,
proving the former real-closure failure boundary.
