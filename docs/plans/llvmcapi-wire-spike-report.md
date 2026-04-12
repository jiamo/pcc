# P6C.2-wire llvmlite → pcc.llvm_capi Spike Report

**Date:** 2026-04-21
**Status:** complete (timebox: 3-5 days allocated, actual ~half day)
**Recommendation:** Route **β4** (text-first IR builder + narrow LLVM-C for verify/JIT)
**Confidence:** medium — materially larger scope than de-PLY

## Goal

Decide how to replace `llvmlite` at the self-host compile path so
pcc's native binary links libLLVM directly and drops the Python-C-
extension dependency. Per user policy, `llvmlite` code stays in the
tree behind a `PCC_USE_LLVMLITE=1` opt-out (same pattern as
`PCC_USE_CPYTHON_AST=1` / `PCC_USE_PLY_C_PARSER=1`).

## Scope survey (live data)

```
llvmlite importers:              73 files
llvmlite APIs used (unique):     ~150 distinct
API call site count:             ~1 159 (ir.* 1050, llvm.* 109)

pcc.llvm_capi today:              32 LLVM-C bindings declared (~21% of need)
```

Broken down by consumer:

| Consumer | `ir.*` usage | `llvm.*` usage | What it does |
|---|---|---|---|
| `pcc/codegen/c_codegen.py` | 698 | 1 | C-frontend IR builder |
| `pcc/py_frontend/codegen/` | 354 | negligible | Python-frontend IR builder |
| `pcc/ir_passes/*` (66 files) | 0 | 109 | Mostly `parse_assembly` + text manipulation |

**Key asymmetry**: the IR-*builder* side (`llvmlite.ir`) is heavy — ~1050 call sites, ~4k LoC of API to replicate. The IR-*binding* side (`llvmlite.binding`) is narrow — mostly `parse_assembly(text) -> ModuleRef` + iteration over `module.functions`.

## Why this is not a de-PLY-style spike

| | de-PLY (M3 α) | llvm_capi wire (M3 β) |
|---|---|---|
| Replace runtime with | frozen static table + driver | hand-written binding + wrapper |
| Preserves upstream work? | yes — p_* bodies reused as-is | no — ir.IRBuilder ≠ LLVM-C |
| Runtime library surface | ~50 methods | ~150 APIs |
| PoC LoC | ~600 (tables + driver) | ~2000-3000 (builder + binding) |
| Chicken-and-egg | none | pcc uses pcc.extern to call LLVM-C; pcc.extern itself compiled by pcc → OK |

There is **no "freeze" analogue** for llvmlite because it's a runtime
*library*, not a *DSL* with compile-time table output. The code in
`ir.IRBuilder.add()` etc. is real imperative IR-construction logic,
not a table dump.

## Routes evaluated

### Route β1 — class-level replacement (drop-in `llvmlite.ir`)

Write `pcc/llvm_capi/ir.py` as a duck-typed clone of `llvmlite.ir`:
`IntType`, `Constant`, `IRBuilder`, `Module`, `Function` — same names,
same method surfaces.

- Codegen call sites don't change.
- Big one-time cost: ~2-3 kLoC of wrapper on top of LLVM-C.
- Risk: matching llvmlite's subtle behaviors (auto-block-termination,
  metadata threading, debug info serialization).
- Estimate: **3-4 weeks**.

### Route β2 — narrow to self-host-only

Keep llvmlite as the CPython-hosted runtime forever; only provide
a minimal LLVM-C path for self-host'd binary.

- Would mean pcc running under CPython goes through llvmlite; pcc
  running as native self-hosted binary goes through llvm_capi.
- Risk: two codegen paths to maintain.
- Doesn't actually reduce total work (both paths need LLVM-C at the
  self-host stage).
- Estimate: **2-3 weeks** (but with permanent dual-path burden).

### Route β3 — auto-generate bindings from llvmlite source

Parse llvmlite.ir source, emit pcc.extern bindings mechanically.

- Deep metaprogramming — llvmlite.ir uses multiple-inheritance +
  dataclass patterns that don't translate cleanly.
- High risk of subtle divergence.
- Estimate: **3-5 weeks**, outcome uncertain.

### Route β4 — text-first IR builder 🌟 recommended

**Key insight:** pcc's codegen ultimately emits IR as text (via
`str(module)`). The ~350-node in-memory IR tree that llvmlite.ir
builds is a *temporary* structure — it gets serialized, verified, and
either JIT'd or passed to ir_passes as text.

Proposal:
- Write `pcc/llvm_capi/ir_text.py` — a *text-emitting* builder with
  the same API as `llvmlite.ir.IRBuilder` / `ir.Constant` / etc.
  Each method appends to an internal string buffer.
- Code like `builder.add(a, b, name="x")` just appends
  `"  %x = add i32 %a, %b\n"` to the current function's buffer.
- Type classes (`ir.IntType(32)`) become stateful tokens, but their
  `.intrinsic_name` etc. are trivially derivable.
- Final `str(module)` is already what our downstream consumes.

Then LLVM-C is only needed for:
- `LLVMParseIRInContext` (verify + get ModuleRef for JIT)
- `LLVMPrintModuleToString` (re-emit after passes, if needed)
- JIT target machine subset (~10 bindings already in pcc.llvm_capi)

Total LLVM-C surface: **~40 bindings** (8 more than today).

The text builder has clear precedent:
- LLVM itself has `raw_ostream`-based text printers for testing
- pcc's own `ir_passes/*` ALREADY operate on text — they parse, mutate
  via regex, re-parse
- This is consistent with how pcc treats IR as a first-class data
  format, not a C++ object graph

**Estimate: 1.5-2 weeks**
- 4 days: write text-emitting replacements for the top-20 most-used
  ir.* classes (IntType, Constant, IRBuilder, Function, etc.)
- 3 days: port codegen — should be zero-touch if API matches
- 3 days: missing-api whack-a-mole + test
- 2 days: `PCC_USE_LLVMLITE=1` opt-out wire + validation

### Route β5 — defer entirely

Given Strategy C's ldd target is "libc + libLLVM" — strictly speaking
linking libLLVM is sufficient; pcc.extern can call LLVM-C directly.
The `llvmlite` dependency is at pcc-development time only (CPython
runs pcc → pcc compiles target). The self-hosted pcc binary produced
by bootstrap doesn't need pcc.llvm_capi for itself — it just needs to
*not* import llvmlite when compiling.

- If pcc is self-hostable via pcc.extern wrapping LLVM-C (for the
  parts the generated native binary needs), then ~350 llvmlite call
  sites in codegen can stay — they're never executed by the native
  binary, only by the CPython-hosted build stage.
- The self-hosted binary would need its own minimal native-side
  codegen path, but that's just the ~10 LLVM-C JIT/emit functions
  we already have.

This route deserves its own spike before β4 commits to the big rewrite.

**Estimate: 1 week for spike, unknown for full M3**.

## Comparison

| Metric | β1 class-level | β4 text-first | β5 defer |
|---|---|---|---|
| New code | 2-3 kLoC | 1-1.5 kLoC | ~200 LoC |
| Changes codegen sites | no | no (same API) | no |
| Self-host achievable | yes | yes | yes, but via different mechanism |
| Risk | high (semantic subtlety) | medium (text format stability) | medium (two codegen paths) |
| Time | 3-4 weeks | **1.5-2 weeks** | 1 week + TBD |

## Recommendation (refined after Codex review)

**β4 (text-first builder) is the sole official route.**

β5 was initially proposed as a way to defer the big rewrite. Codex's
critique caught the fundamental issue: even β5 must eventually handle
the full `ir.*` surface because P6C.6's three-stage bootstrap
(`./pcc2 pcc.py -o pcc3`) requires the self-hosted binary to compile
pcc itself — which means covering every `ir.*` call site in pcc's
own source. β5's "savings" are illusory; it just relocates the work
and adds a permanent dual-path maintenance cost.

β5 survives as **β4.0**: a surface-tracing sub-stage that maps the
actual API coverage before committing to implementation order.

β1 is rejected — it's writing another llvmlite, not removing llvmlite.

## Parity gates (three-level, not byte-identical from day 1)

Codex flagged that byte-identical IR output as a phase-1 gate would
turn β4 into a formatting exercise. The actual gates we need are:

### Level 1 — Semantic equivalence (phase-1 blocker)

- `LLVMParseIRInContext` on emitted text succeeds
- `LLVMVerifyModule` returns no errors
- Runtime output matches for test corpus
- Focused IR regressions (ir_passes tests) pass

### Level 2 — Structural equivalence (phase-2 blocker)

- CFG / block / phi / type / attribute shape matches after
  canonicalization
- Not byte-identical text, but post-canonicalization-identical

### Level 3 — Textual alignment (phase-3 polish)

- `!0` / `!1` metadata numbering matches
- DWARF debug info ordering matches
- Formatting / naming as close to llvmlite as practical

**Critical**: Level 3 is NOT a blocker for the default-flip; it's
stabilization work after the main path is green.

## β4 phased breakdown

### β4.0 — Surface trace (1 week)

What β5 would have been, as a pure data-gathering step:

- Run `pcc self-compile` under instrumentation, log every `ir.*` /
  `llvm.*` call site with frequency
- Partition: codegen core / Python frontend / metadata-only / debug-
  only / long-tail
- Produce `docs/plans/llvmlite-api-surface.md` with the actual API
  coverage list

Output feeds β4.1 backlog ordering. Cannot be "wasted" — it's
prerequisite mapping either way.

### β4.1 — No-debug core text builder (4-5 days)

Implement text-emitting replacements for the top-20 most-used types
identified in β4.0. Expected set:

- `Module`, `Function`, `BasicBlock`, `IRBuilder`
- Common `Type`: `IntType`, `PointerType`, `VoidType`, `DoubleType`
- Common `Constant`: int, real, string, null, array, struct
- Common instructions: call, phi, branch, gep, load, store, cast,
  arithmetic (add/sub/mul/div/shl/ashr/and/or/xor)
- `GlobalVariable` with simple initializers

Gate (Level 1):

- C front-end main path compiles `tests/py_corpus/phase1` corpus
- `LLVMParseIRInContext` + `LLVMVerifyModule` pass on output
- Runtime results match PLY-built (already passing) output
- **No debug info required yet** — `ir.DIToken`, `module.add_debug_info`
  can raise NotImplementedError on first pass

### β4.2 — LLVM-C verify/JIT/object closed loop (3 days)

Pipe β4.1's text output through real LLVM-C:

- `parse_assembly` (already in pcc.llvm_capi)
- `verify` (already in pcc.llvm_capi)
- JIT / emit_object / emit_asm
- Target machine setup

Replaces `llvmlite.binding` on the default path. Surface is narrow
(~10 bindings) because ir_passes already mostly operate on text.

### β4.3 — Metadata / DWARF / long-tail alignment (3-5 days)

Only after β4.2 is green, iterate on Level 3 textual alignment:

- DIFile / DISubprogram / DILocation
- Metadata auto-numbering
- Debug builder conveniences
- Remaining long-tail `ir.*` APIs

### β4.4 — Flip default + regression gate (2 days)

- `PCC_USE_LLVMLITE=1` becomes reverse-opt-out (pattern mirrors
  `PCC_USE_CPYTHON_AST=1` / `PCC_USE_PLY_C_PARSER=1`)
- Full regression suite under new default
- Byte-identical IR check optional at this stage — Level 2 structural
  equivalence is the bar

## Updated time estimate

- β4.0: 1 week (trace)
- β4.1: 4-5 days (core builder, no debug)
- β4.2: 3 days (verify + JIT)
- β4.3: 3-5 days (metadata + DWARF + long-tail)
- β4.4: 2 days (flip + regression)

**Total: 3-3.5 weeks.** More than the initial 1.5-2 week estimate
because Codex's review rightly isolated phases to avoid the "perfect
first pass" trap.

## Scope exclusions

- **Self-backend replacement** (replacing LLVM machine-code-emitter)
  is NOT part of this epic. That's a separate post-P6C effort.
  β4 only replaces `llvmlite` Python shim; `libLLVM` still provides
  machine code generation.
- **Byte-identical output** is NOT a β4 exit gate; it's a polish
  target in β4.3 with no hard SLA.
- **llvmlite deletion** is NOT a β4 exit gate; the code stays as
  `PCC_USE_LLVMLITE=1` opt-out permanently (user policy).

## Exit criteria (hardened)

For the actual implementation epic (post-spike):

- ✅ `ldd pcc` shows only `libc.*`, `libLLVM*` (no libpython, no llvmlite `.so`)
- ✅ Default codegen path (opt-out `PCC_USE_LLVMLITE=1`) produces
  byte-identical IR to the llvmlite path on:
  - all 63 oracle C snippets
  - all 103 py_corpus programs (phase1+2+3+6c)
  - 200+ csmith fuzz seeds
- ✅ Performance regression ≤ 10% vs llvmlite baseline
- ✅ llvmlite code paths preserved in source tree (user policy)
- ✅ No new audit blockers introduced

## Decision checkpoint

**Block #139 until user reviews this report.** Unlike de-PLY, this
spike shows a materially larger scope with multiple viable paths;
the user should pick the tradeoff preference (β4 conservative +
shorter vs β5 defer + lower initial investment).

## Open questions

1. **IR format stability**: we assume LLVM IR text format is stable
   enough that emitting it by hand won't drift. How much does LLVM
   20.x's output differ from 19.x? Needs a quick check on the LLVM
   IR textual-reference commit history.

2. **Metadata ordering**: llvmlite auto-numbers `!0`, `!1` etc.
   metadata nodes. A text builder needs the same behavior for
   byte-identical output.

3. **Debug info (DWARF)**: `c_codegen.py` emits DWARF metadata via
   `ir.DIToken` / `module.add_debug_info`. Text-emitting this
   requires careful DISubprogram / DIFile / DILocation serialization.

4. **GlobalVariable initializer serialization**: llvmlite handles
   struct / array literal printing in a specific order; need to
   match.

5. **When is pcc's own native binary actually executed vs. CPython**:
   if only compiling target C files via CPython-hosted pcc is the
   normal flow, β5 becomes more attractive.

These are tractable during the spike/implementation but worth
flagging before pulling the trigger on β4.
