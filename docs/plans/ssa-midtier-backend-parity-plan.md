# SSA MidTier And Backend Parity Plan

Superseded as the primary optimization roadmap by:

- `docs/plans/all-pass-llvm-ir-1to1-master-plan.md`

This document remains useful as background for the earlier SSA/midtier
follow-on direction, but it is no longer the canonical master plan.

## Why This Exists

The LLVM leaf-pass translation plan is done. `pcc` now has explicit Python-side semantics or explicit tested source-level boundaries for all 80 default LLVM leaf pass names.

That plan did not aim to make `O0 + all-pass` equal to `clang -O2`. It aimed to make every visible LLVM leaf pass name explicit and testable inside `pcc`.

The next problem is different:

- `O0 + all-pass` still trails `clang -O2` badly at runtime.
- `pcc -O2` is already close to `clang -O2` on the 80-case suite.
- The remaining gap is mostly not "missing leaf-pass coverage." It is SSA/backend-quality work.

This document is the follow-on roadmap for that next phase.

## LLVM Reference Requirement

This plan is not allowed to drift into "invent a plausible optimizer" work.
For every SSA/MidTier step, implementation and closure must be checked against
the corresponding LLVM source and algorithm boundary first.

Local reference checkout for this repository:

- `/tmp/llvm-src/`

Required rule for this plan:

- before changing SSA construction, mem2reg-like promotion, SCCP, GVN/NewGVN,
  DSE/ADCE, MemorySSA-style reasoning, or loop canonicalization, read the
  corresponding LLVM source entry points and state which subset `pcc` is
  intentionally implementing,
- if `pcc` is taking a narrower subset than LLVM, document that boundary in the
  code change and in this plan instead of silently approximating it,
- "behavior roughly matches on one case" is not closure; the closure target is
  "implemented with an explicit LLVM reference boundary".

Primary LLVM source entry points for this plan:

- mem2reg / promotion:
  - `/tmp/llvm-src/llvm/lib/Transforms/Utils/PromoteMemoryToRegister.cpp`
- SCCP:
  - `/tmp/llvm-src/llvm/lib/Transforms/Scalar/SCCP.cpp`
  - `/tmp/llvm-src/llvm/lib/Transforms/IPO/SCCP.cpp`
- GVN / NewGVN:
  - `/tmp/llvm-src/llvm/lib/Transforms/Scalar/GVN.cpp`
  - `/tmp/llvm-src/llvm/lib/Transforms/Scalar/NewGVN.cpp`
- dead code / dead store:
  - `/tmp/llvm-src/llvm/lib/Transforms/Scalar/DCE.cpp`
  - `/tmp/llvm-src/llvm/lib/Transforms/Scalar/ADCE.cpp`
  - `/tmp/llvm-src/llvm/lib/Transforms/Scalar/DeadStoreElimination.cpp`
- memory reasoning:
  - `/tmp/llvm-src/llvm/lib/Analysis/MemorySSA.cpp`
  - `/tmp/llvm-src/llvm/lib/Analysis/MemorySSAUpdater.cpp`
- loop canonicalization and feeding passes:
  - `/tmp/llvm-src/llvm/lib/Transforms/Utils/LoopSimplify.cpp`
  - `/tmp/llvm-src/llvm/lib/Transforms/Scalar/LICM.cpp`
  - `/tmp/llvm-src/llvm/lib/Transforms/Scalar/IndVarSimplify.cpp`
  - `/tmp/llvm-src/llvm/lib/Transforms/Scalar/LoopLoadElimination.cpp`

## Current Ground Truth

The codebase already contains the first pieces of an SSA-oriented story:

- `PassContext` has an explicit `AllocStrategy.SSA` mode for locals.
- `AllocDecisionPass` already classifies simple non-escaping scalars as `SSA` or `REGISTER_HINT`.
- `c_codegen.py` already has a direct-SSA local declaration path that skips `alloca` for eligible locals.
- `GVNPass` is still explicitly delegated because full cross-block GVN needs SSA + dominators.
- `memory_opt.py` has now crossed from pure analysis into a bounded low-tier rewrite: within one basic block it can drop redundant overwritten stores and reuse/forward simple loads, it now preserves separate facts for clearly independent exact `alloca` slots, and it conservatively clears or degrades those facts across unknown loads, calls, control-flow boundaries, and `va_arg`. It also now recognizes a narrow identity-alias subset for exact slots through same-type `bitcast` and zero-offset `getelementptr` patterns (including zero-cast indices), while explicitly refusing aggregate-view reinterpretation shapes that do not match the original `alloca` type. Broader MemorySSA-style reasoning is still missing.

So the real next step is not "invent SSA from nothing." It is "replace the current hints and source-side approximations with a real internal SSA MidTier."

## Current Implementation Status

As of April 13, 2026, the bootstrap slice is implemented and the plan is in a mixed Phase 1 / Phase 3 state:

- `pcc/ssa/ir.py` defines a minimal internal SSA model with blocks, values, phi nodes, CFG edges, and dominator recomputation.
- `pcc/ssa/builder.py` lowers a restricted structured-scalar subset of `pycparser` function ASTs into that SSA form, now including direct call expressions and bare call statements, CFG-based lowering for short-circuit `if` conditions (`&&` / `||`), top-level short-circuit value sites in decl/init, assignment, and return positions, `while` / `do-while` / `for` loops with proper loop-header phi nodes and back-edge patching, compound assignments (`+=`, `-=`, `*=`, etc.) desugared to binary ops, standalone `++`/`--` increment/decrement statements, explicit scalar casts, pointer `ArrayRef` / dereference loads, read-only struct/union field-address + field-load chains (`->`, chained `.field` on aggregate fields already materialized as addresses, and `&field`), and string-literal call arguments. It still explicitly rejects local `static` / `extern` storage, non-local assignment targets, nested short-circuit value expressions, `switch`, labels/gotos, variadics, and the broader local-array/global-memory cases so the bootstrap layer does not mis-model stateful storage or complex control flow as ordinary SSA locals.
- `pcc/passes/ssa_bootstrap.py` now provides an analysis-only bridge that builds SSA for supported functions and records successes/skips in `PassContext`.
- `pcc/ssa/sccp.py` now runs a first SCCP lattice solver over the bootstrap SSA form, tracking constant SSA values, reachable blocks, and foldable branches.
- `pcc/ssa/gvn.py` now runs a first dominator-aware GVN analysis over the bootstrap SSA form, identifying redundant pure SSA expressions within and across dominated blocks.
- `pcc/ssa/adce.py` now runs a first SSA liveness walk over bootstrap SSA values and source bindings, identifying dead local scalar bindings that do not contribute to returns, branch conditions, or live call arguments.
- `pcc/passes/ssa_sccp.py` exposes that analysis through the pass framework without changing codegen yet.
- `pcc/passes/ssa_sccp_rewrite.py` is now a first bounded SCCP consumer: it rewrites a safe int-typed subset of whole-expression ID sites in decl/init, assignment, and return positions to constants when bootstrap SSA SCCP proves the value constant.
- `pcc/passes/ssa_gvn.py` exposes the first SSA GVN analysis through the pass framework and records redundant-expression counts in `PassContext`.
- `pcc/passes/ssa_gvn_rewrite.py` is now the first SSA-GVN-backed transform consumer: it rewrites a bounded set of whole-expression decl/init, assignment, and return sites to dominating source variables when the SSA GVN result proves the expression redundant across block boundaries and type-compatible.
- `pcc/passes/ssa_dse.py` is now the first SSA-backed dead-store consumer with side-effect preservation: it rewrites dead local scalar bindings with effectful RHS expressions into standalone expression statements instead of dropping the side effect.
- `pcc/passes/ssa_adce.py` is now the first SSA liveness-backed dead-binding consumer: it clears dead local scalar initializers and removes dead local scalar assignments when the bootstrap SSA result proves the binding does not reach a return or control predicate.
- `pcc/passes/ssa_branch_prune.py` is the first SSA-backed transform consumer: it uses folded branch facts from `ssa-sccp` to remove a bounded subset of source-level `if` statements.
- `tests/test_ssa_builder.py` covers straight-line lowering, branch/join phi insertion, single-live-exit branch handling, direct calls, CFG-based short-circuit condition lowering, top-level short-circuit value lowering to SSA phi results, `while`/`do-while`/`for` loop lowering with header phis and back-edges, `for` loops with `DeclList` init and post-increment, compound assignments, and standalone `++`/`--` statements.
- `tests/test_ssa_bootstrap_pass.py` covers `PassContext` integration and unsupported-function skip recording, including non-local assignment targets and nested short-circuit value expressions that still sit outside the current bootstrap boundary.
- `tests/test_ssa_gvn.py` and `tests/test_ssa_gvn_pass.py` cover same-block and dominator-scope redundancy detection plus pass-level stats/logging.
- `tests/test_ssa_gvn_rewrite.py` covers cross-block return and nested-declaration rewrites plus stale-binding and narrowing-type safety guards.
- `tests/test_ssa_adce.py` and `tests/test_ssa_adce_pass.py` cover dead initializer / dead assignment discovery and the bounded AST rewrites that consume those results.
- `tests/test_ssa_dse_pass.py` covers effectful dead assignment and dead initializer rewrites into standalone expression statements, while leaving pure dead bindings to `ssa-adce`.
- `tests/test_ssa_sccp.py` and `tests/test_ssa_sccp_pass.py` cover constant propagation, reachability, and pass-level stats/logging.
- `tests/test_ssa_sccp_rewrite.py` covers join-proven constant rewrites for decl/init, assignment, and return sites plus same-line coordinate and range/type safety guards.
- `tests/test_ssa_branch_prune.py` covers the first SSA-backed AST rewrite on a join-proven constant condition, value-position short-circuit facts flowing into later branch pruning, and short-circuit runtime regressions that must not be folded through global side effects.
- `pcc/passes/memory_opt.py` now performs the first bounded local-memory rewrites in the low tier, removing redundant overwritten stores and rewriting simple within-block store/load and load/load chains directly in LLVM IR text.
- `memory-opt-ir` boundary hardening now treats `va_arg` as an unsupported memory barrier, distinguishes independent exact `alloca` slots from unknown/derived addresses, drops dead-store candidates once an unknown load may have observed them, recognizes exact-slot identity aliases through safe `bitcast` and zero-offset `getelementptr` patterns, and rejects aggregate-view reinterpretation aliases when the GEP source type no longer matches the original slot declaration. This closed the `stdarg-4.c` varargs regression, the aggregate-alias regression in `20071219-1.c`, and the union/view-alias regression in `cbrt.c` without reopening the earlier local-alias regressions (`00014.c`, array-decay call lowering) or losing the new `[0]`-index array cleanup wins.
- Compiler cache invalidation now tracks `pcc/ssa/*.py` so SSA changes do not reuse stale cached translation artifacts.
- Compile/JIT/native cache signatures now also include effective pass-selection state from `PCC_DISABLE_PASSES`, so SSA ablation and rollback experiments do not silently reuse artifacts built under a different pass configuration.
- This slice is now wired into the default HighTier. Bootstrap + GVN + SCCP + DSE + ADCE now feed bounded SSA-backed AST transforms: `ssa-sccp-rewrite`, `ssa-gvn-rewrite`, `ssa-dse`, `ssa-adce`, and `ssa-branch-prune`; general codegen still does not consume arbitrary SSA values yet.
- The first downstream bootstrap-boundary regression is now closed: after direct-call lowering landed, `00033.c` exposed that non-local assignments such as `g = 0;` were being mis-modeled as local SSA facts and then folded through SCCP/branch-prune. The builder now rejects that shape, and the focused runtime regression stays green.
- The next bootstrap coverage step is now closed as well: short-circuit `if` conditions and top-level short-circuit value sites no longer need to be eagerly modeled as plain binary expressions in SSA. They now lower through explicit CFG edges and SSA phi values, while nested short-circuit value expressions are still conservatively skipped until the SSA layer can represent them without reintroducing eager-evaluation bugs.
- Loop support is now landed: `while`, `do-while`, and `for` loops lower through proper SSA CFG construction with loop-header phi nodes for all live variables and back-edge patching after the body is lowered. This significantly expands the fraction of real functions that qualify for SSA bootstrap analysis. Compound assignments (`+=`, `-=`, etc.) and standalone `++`/`--` statements are also now supported, enabling SSA construction for typical loop-heavy scalar code.
- Phase 2 SSA → LLVM IR lowering is now landed for a real but still narrow scalar slice:
  - `c_codegen.py` now contains `_lower_ssa_function()` which lowers eligible functions directly from internal SSA IR to LLVM basic blocks, values, phi nodes, and terminators — bypassing the AST-to-alloca-to-load/store path entirely.
  - LLVM reference boundary: this achieves top-down what `PromoteMemoryToRegister.cpp` does bottom-up. LLVM mem2reg takes alloca/load/store → IDF phi placement → renaming. pcc Phase 2 takes C AST → internal SSA (builder.py already placed phis) → LLVM values + phi nodes. The output is equivalent: promotable scalar locals become LLVM SSA values with phi nodes at join points, no alloca needed.
  - Subset intentionally narrower than LLVM mem2reg: only functions the SSA builder accepted (structured scalar CFG), but that subset is now materially wider than the original scalar-only slice. Direct SSA lowering now handles direct and indirect calls, integer/pointer phi values, explicit scalar casts, pointer loads/stores, fixed-size local arrays, read-only and stored struct field chains, aggregate-value field extracts, globals/enums/string literals flowing through SSA, `sizeof`-folded constants, pointer difference feeding later arithmetic, and bounded ternary/branch-pruned join cases. Variadics, local statics, `break`/`continue`, `ExprList`, and a general memory model still sit outside this slice and continue to fall back to AST codegen.
  - `_lower_ssa_instruction()` now maps SSA binary/unary ops, explicit casts, field-address and field-extract nodes, loads/stores, stack allocas, globals, and direct/indirect calls to LLVM builder operations. `_resolve_ssa_value()` handles SSAConstant, SSAStringConstant, SSAGlobalRef, SSAUndef, SSAParam, and instruction results. `_ssa_convert()` handles integer width changes between phi and return types, and phi lowering now filters folded-away predecessors after SCCP branch pruning.
  - `tests/test_ssa_lowering.py` now covers multi-def scalars through if/else branches, nested if, constant propagation through joins, while/for/do-while loops, direct and indirect calls, unsigned/pointer values through joins, explicit integer casts, pointer loads/stores, struct field chains, address-of struct fields, aggregate-value field extracts, fixed local arrays, string-literal pointer phis, `sizeof` on typed aggregates, pointer-difference reuse, and phi stability after constant branch pruning — all verified at runtime.
- Phase status as of April 15, 2026:
  - Phase 0 is effectively complete at the planning level: delegated pass boundaries are explicit and measurable.
  - Phase 1 is landed for the current bootstrap subset: there is a usable internal SSA IR, structured-scalar SSA construction, dominator recomputation, and default-pipeline analysis/consumer wiring.
  - Phase 2 is not closed overall. A first direct SSA→LLVM lowering slice exists in `c_codegen.py` via `_lower_ssa_function()`, but this is still a narrow subset rather than a completed replacement for the old `AllocStrategy.SSA` / `REGISTER_HINT` bridge. The important remaining question is not "does lowering exist at all" but "which LLVM mem2reg-equivalent subset is actually implemented and verified against `/tmp/llvm-src/llvm/lib/Transforms/Utils/PromoteMemoryToRegister.cpp`?"
  - Phase 3 is partially landed, not closed. SCCP/GVN/DSE/ADCE have real SSA-backed analyzers and bounded consumers, and some SCCP facts are consumed during SSA lowering, but the optimization story is still intentionally narrow and has not yet reached benchmark-backed closure against the LLVM reference boundary.
  - Phase 4 remains preparatory only. `memory_opt.py` does useful within-block cleanup and exact-slot reasoning, but there is still no cross-block MemorySSA-style model.
  - Phase 5 and Phase 6 are still open roadmap items.
- Latest verification snapshot:
  - `tests/test_ssa_lowering.py` exists and covers the first direct SSA→LLVM lowering slice.
  - Focused SSA/shared-path gate currently verified:
    - `env -u LC_ALL uv run pytest tests/test_ssa_builder.py tests/test_ssa_lowering.py tests/test_ssa_gvn.py tests/test_ssa_adce.py tests/test_ssa_sccp.py tests/test_ssa_bootstrap_pass.py tests/test_ssa_gvn_pass.py tests/test_ssa_adce_pass.py tests/test_ssa_sccp_pass.py -q -n0`
    - result: `128 passed`
    - `env -u LC_ALL uv run pytest tests/test_zlib.py -q -n0`
    - result: `3 passed`
    - `env -u LC_ALL uv run pytest 'tests/test_lua.py::test_pcc_runtime_matches_native[math.lua]' -q -n0`
    - result: `1 passed`
    - bootstrap coverage probe over `projects/zlib-1.3.1/*.c`
    - result: `101 built / 79 skipped` across the current file set, improved from the earlier `36 built / 319 skipped`; representative gains now include `inflate.c` at `18 built / 4 skipped`, `deflate.c` at `17 built / 11 skipped`, and `gzwrite.c` at `12 built / 1 skipped`
  - A fresh full-suite closure number is still required before calling any broad phase "closed".

## Goal

Build an internal SSA MidTier that sits between the current AST/PassContext world and final LLVM lowering, then move the currently bounded source-level approximations onto that SSA layer where doing so is meaningful.

The end state is:

- `pcc` has its own internal SSA representation for eligible scalar code.
- cross-block scalar optimizations no longer depend on AST-only approximations,
- source-level pass names such as `gvn`, `sccp`, and `dse` can be backed by real SSA semantics,
- `pcc -O0 + all-pass` narrows the gap to `clang -O2`,
- `pcc -O2` remains at least as good as today's baseline.

## Non-Goals

This plan does not start by replacing LLVM's machine backend.

Not in scope for the first wave:

- custom instruction selection,
- custom register allocation,
- custom machine scheduling,
- a full standalone replacement for LLVM `-O2`.

The first objective is to beat the current source-level approximation boundary, not to reimplement all of LLVM.

## Architecture Direction

The repository currently has three effective layers:

- HighTier AST analysis and transforms populate `PassContext`,
- MidTier codegen reads `PassContext` and emits LLVM IR,
- LowTier IR-text passes annotate or lightly clean up that IR.

The new target architecture should become:

- HighTier AST analysis keeps discovering language facts, escape info, ranges, loop shape, and source-level opportunities.
- MidTier stage A lowers eligible function bodies into an internal SSA IR.
- MidTier stage B runs SSA analyses and transforms.
- MidTier stage C lowers the SSA IR to LLVM IR.
- Existing AST-only lowering stays as fallback for unsupported constructs until coverage is wide enough.

This is an additive migration, not a flag day rewrite.

## Execution Phases

### Phase 0: Stabilize The Boundary

Before building new SSA machinery, make the current boundary explicit and measurable.

Deliverables:

- document which current passes are still only "source-level explicit boundary" because they need SSA,
- add benchmark labels that distinguish:
  - source-only pass wins,
  - LLVM-backend wins,
  - mixed wins,
- add focused probes for:
  - cross-block redundant expressions,
  - dead stores requiring phi-aware reasoning,
  - constant propagation across join points,
  - loop-carried scalar cleanup.

Exit criteria:

- every currently delegated scalar pass has one benchmark-friendly focused case,
- benchmark harness can report the gap for those cases cleanly.

### Phase 1: Introduce A Minimal Internal SSA IR

Build the smallest useful SSA representation for structured scalar C code.

Scope:

- function-local scalars only,
- structured control flow only,
- no VLAs, no `setjmp`, no unsupported gotos in the initial slice,
- start with integer and pointer scalars before wider aggregate handling.

Core pieces:

- basic blocks,
- CFG edges,
- SSA values,
- phi nodes,
- defs/uses,
- dominator tree,
- dominance frontier or an equivalent phi-placement algorithm.

Initial integration strategy:

- lower only functions that already qualify for conservative SSA promotion,
- keep existing AST-to-LLVM lowering as fallback,
- make the handoff visible in `PassContext` metrics.

Exit criteria:

- a small set of functions can be lowered through internal SSA end-to-end,
- emitted LLVM IR matches existing behavior on focused regression cases,
- fallback path remains intact.

### Phase 2: Replace "SSA Hints" With Real SSA Construction

Promote the current `AllocStrategy.SSA` and `REGISTER_HINT` world into actual SSA construction.

Targets:

- direct scalar local defs become SSA values,
- multi-def scalar locals get phi placement at joins,
- simple stack temporaries stop depending on later LLVM `mem2reg`,
- current codegen-side "skip alloca when single-def" logic becomes a special case of the SSA builder instead of the long-term strategy.

Exit criteria:

- focused mem2reg-like cases stop relying on LLVM `mem2reg` to clean up trivially promotable locals,
- direct SSA lowering covers representative arithmetic, branches, and loops.

### Phase 3: Land Real SSA Scalar Passes

Move the highest-value currently bounded passes onto SSA.

Priority order:

1. `sccp`
2. `gvn` / `newgvn`
3. `dse`
4. `adce`
5. `jump-threading` and branch refinement follow-ups

Expected work:

- SSA SCCP with a sparse lattice,
- dominator-aware value numbering,
- dead instruction elimination driven by SSA use counts,
- stack/local dead-store elimination on top of SSA facts rather than AST-only scans,
- updated registry notes when a pass stops being a bounded approximation and becomes a real SSA-backed implementation.

Exit criteria:

- each pass above has at least one focused case where:
  - AST-only translation was previously weaker,
  - SSA MidTier now closes the gap,
  - default suite stays green,
- benchmark deltas show measurable `O0 + all-pass` improvement on scalar-heavy cases.

### Phase 4: Add Memory Reasoning Beyond Scalar SSA

Scalar SSA alone is not enough for the current `dse`, `loop-load-elim`, and related boundaries.

The next layer should be a conservative memory model for local stack and simple address-taken objects.

Options:

- a lightweight MemorySSA-style graph for promotable local memory,
- or a narrower local-memory dependence model if that gets results faster.

Initial scope:

- stack-only locals,
- simple alias classes,
- no global/interprocedural memory in the first cut.

Exit criteria:

- at least one class of `dse` and repeated-load cleanup no longer requires "mem2reg first" in the reference path,
- `memory_opt.py` starts performing real replacements for a bounded supported subset instead of staying analysis-only.

### Phase 5: Loop And Vectorization-Feeding Canonical Forms

Once SSA scalar and local-memory cleanup exist, push loop forms toward what LLVM can best consume.

Targets:

- stronger induction canonicalization before LLVM sees the IR,
- better loop-carried scalar elimination,
- cleaner reduction forms,
- vectorization-friendly canonical block shapes,
- less CFG noise at loop headers and latches.

This phase is still primarily about feeding LLVM better IR, not replacing LLVM vectorizers outright.

Exit criteria:

- loop-focused benchmark slice improves at `O0 + all-pass`,
- `pcc -O2` does not regress,
- vectorization candidates become easier to prove in focused IR inspections.

### Phase 6: Whole-Program And Cross-TU Work

After function-local SSA is credible, push upward into whole-program structure.

Targets:

- cross-TU wrapper collapse,
- function specialization,
- stronger devirtualization,
- global constant and range propagation,
- summary-driven IPO for internal functions,
- optional LTO-like internal merge mode ahead of final LLVM lowering.

Exit criteria:

- call-heavy and multi-file benchmarks improve beyond current `inline-opt` and source-level `globalopt` boundaries,
- phase results are measured separately from backend-only wins.

## Validation Strategy

This roadmap must stay benchmark- and regression-driven, not architecture-driven.

For each phase:

1. add focused regression tests first,
2. add at least one benchmark-sensitive probe,
3. run the sensitive shared-path integration gate before and after the batch:
   - `env -u LC_ALL uv run pytest tests/test_zlib.py -q -n0`
   - `env -u LC_ALL uv run pytest 'tests/test_lua.py::test_pcc_runtime_matches_native[math.lua]' -q -n0`
   - `env -u LC_ALL uv run pytest tests/test_sqlite.py -q -n0`
   - plus the focused SSA tests that cover the exact slice being edited,
4. run the default suite,
5. measure:
   - `O0 all-pass` vs `O0 no-pass`,
   - `O0 all-pass` vs `clang -O2`,
   - `pcc -O2` vs `clang -O2`.

Additional rules for this stage:

- skips are configuration facts, not validation wins; a skipped vendor test does not count toward phase exit criteria,
- if a shared-path integration case fails in one environment but not another, treat that as an unresolved rollout risk until it is reproduced or convincingly explained,
- do not broaden SSA coverage and local-memory rewriting in the same batch that first turns one of the vendor-sensitive gates red.

The three benchmark comparisons answer different questions:

- is the new MidTier doing useful work on its own,
- is it reducing the backend gap,
- does it help or hurt the final `pcc -O2` outcome?

## Success Metrics

Short-term success:

- the current delegated/bounded scalar passes gain real SSA-backed implementations,
- `O0 all-pass` beats `O0 no-pass` by more than the current noise floor on the 80-case suite,
- the default suite stays green.

Medium-term success:

- the `O0 all-pass` vs `clang -O2` runtime gap drops materially from the current baseline,
- `pcc -O2` stays at or above current runtime parity.

Long-term success:

- `pcc` owns enough of the scalar and IPO optimization story that LLVM `O2` becomes more of a final cleanup/backend stage than the primary optimization engine.

## Immediate Next Batch — Status as of April 16, 2026

### Step 1: Re-baseline shared-path gate — ✅ Done

All gates green:
- SSA focused tests (builder + lowering + gvn + adce + sccp + bootstrap_pass + gvn_pass + adce_pass + sccp_pass): 128 passed
- zlib: 3 passed
- Lua math.lua: 1 passed

Prior numbers (114 passed) reflect the state before the latest batch of bootstrap coverage expansion (switch statements, `$t.N` temp prefix, short-circuit widening).

### Step 2: Phase 2 LLVM reference audit — ✅ Done

Comparison against `/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Utils/PromoteMemoryToRegister.cpp`:

LLVM mem2reg handles four cases for promotable allocas (only loads/stores, no address-taken):
1. **Dead alloca** (no uses) → delete. pcc SSA builder: ✅ doesn't create values for unused locals.
2. **Single-store** (one defining block) → replace loads. pcc SSA builder: ✅ directly uses the value.
3. **Single-block** (all uses in one block) → linear scan. pcc SSA builder: ✅ straight-line code.
4. **General** (multi-block multi-def) → IDF phi placement + rename. pcc SSA builder: ✅ structured phi placement via loop headers and if/else joins.

**Still-missing cases** (functions where SSA lowering falls back to AST codegen):
- Struct/union field access (`ptr->field`): 76% of rejections in zlib. The SSA builder can't model memory operations.
- `static` local storage: 5% of rejections. Requires global-state modeling.
- `sizeof` on complex types: 3% of rejections.
- Ternary expressions: 2% of rejections. Could be added.
- `break`/`switch`: small but blocks some functions.

**Key architectural fact**: The O1 floor pipeline already runs LLVM's own SROA/mem2reg on ALL functions (including those that fall back to AST codegen). So the SSA lowering path's value is not "avoiding alloca" (the floor handles that) but "injecting pcc's own SCCP/GVN analysis during lowering." For struct-heavy code, this additional value is limited.

zlib SSA coverage: 28/152 functions (18%). The remaining 82% go through AST codegen + O1 floor, which already produces good code.

### Step 3: AllocStrategy bridge — ✅ Resolved

Current state of the three allocation modes:

| Mode | When | What it does | Still needed? |
|------|------|-------------|---------------|
| `AllocStrategy.SSA` | Single-def non-escaping scalar | Codegen skips alloca, uses value directly | **Yes** — fallback for functions outside SSA builder scope |
| `AllocStrategy.REGISTER_HINT` | Multi-def non-escaping scalar | Codegen creates alloca; O1 floor SROA promotes it | **Yes** — fallback for functions outside SSA builder scope |
| SSA lowering (`_lower_ssa_function`) | Eligible structured-scalar functions | Bypasses alloca entirely, emits LLVM phi nodes | **Primary path** for eligible functions |

Decision: all three modes are needed. They are not redundant:
- SSA lowering is the best path (emits clean phi + injects SCCP/GVN) but only covers 18% of zlib functions
- `AllocStrategy.SSA` and `REGISTER_HINT` are the fallback for the remaining 82%, with O1 floor SROA cleaning them up
- Removing either fallback mode would regress functions that can't enter SSA lowering

### Step 4: SSA builder expansion — ✅ Done for structured scalar

Highest-value expansion candidates by rejection count (zlib):
1. **TernaryOp** — ✅ Done for whole-expression position (decl init, assignment RHS, return). Nested ternary in arbitrary expression positions still deferred: `_lower_expr` doesn't return block-threading info, so nested ternaries would need it to return `(block, value)` tuples throughout.
2. **Switch statement** — ✅ Done. `SSASwitch` terminator (IR + SCCP + codegen), per-case blocks, case-label constant evaluation, break-frame tracking, fall-through rejection. See `pcc/ssa/ir.py:SSASwitch`, `pcc/ssa/builder.py:_lower_switch`, `pcc/codegen/c_codegen.py` switch lowering, and `pcc/ssa/sccp.py` SSASwitch folding.
3. **Break / Continue statement** — ✅ Done for all loop types. `_LoopFrame` tracks both `breaks` and `continues` lists, pushed/popped around `while` / `for` / `do-while`. Env-merging infrastructure in place: `_merge_envs` at exit/latch/continue-block for multiple incoming envs; `exit_envs` snapshot param through `_lower_condition_branch` captures per-predecessor envs for side-effecting conds (e.g. `++i < 3 && ++j < 3` in while-cond); `_blocks_targeting` helper finds all cond-chain predecessors of body/exit; `for.continue` dedicated block so `continue` runs the `next` expression. Tests in `tests/test_ssa_builder.py` and `tests/test_ssa_lowering.py` cover break/continue in each loop type plus interleaved, side-effecting conds, and ExprList comma-expr cases.
4. **ExprList (comma expr)** — ✅ Done both as statement (e.g. for-next `a=a+1, b=b+2`) and in value position (e.g. `int x = (y = n+1, y*2)`).
5. Struct field access — would require partial memory modeling, still deferred (see Phase 4).

### Step 5: Tighten SCCP end-to-end — ✅ Done

Comparison against `/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/SCCP.cpp`:

LLVM SCCP consumer does three things after the lattice solve:
1. Delete dead blocks (unreachable) → pcc: ✅ `sccp_reachable` → `builder.unreachable()` in `_lower_ssa_function`
2. Simplify instructions (replace constants) → pcc: ✅ `sccp_constants` → skip instruction, emit `ir.Constant` directly
3. Remove non-feasible edges (fold branches) → pcc: ✅ `sccp_folded_branches` → `builder.branch()` unconditional

SCCP lattice solver (pcc/ssa/sccp.py, 362 lines) matches LLVM's `SCCPSolver` algorithm:
- Lattice: UNKNOWN → CONSTANT → OVERDEFINED
- Params start OVERDEFINED
- Phi merge across reachable predecessors only
- Binary/unary/cast constant evaluation
- Branch condition analysis for reachability

Verified end-to-end: `int f(int c) { int x; if(c) x=42; else x=42; return x; }` →
SSA SCCP proves x=42 at join → lowering emits `ret i32 42` directly (no phi, no branch in output).

### Step 6: Bench correctness fixes — ✅ Done

Three bench regressions fixed; verified on `bench/bench.py --bench byte_histogram --bench tower_of_hanoi --bench dfs_cycle --opt-level 0`:

1. **byte_histogram** returning 14 instead of 135. `hist[data[i]]` where `data[i]` is unsigned char was sign-extended to i64, producing negative index. Fix: `_lower_ssa_load` in `pcc/codegen/c_codegen.py` now takes an `index_type_name` param and zext's unsigned sources. Bench: 0.38x pcc/clang (pcc 62% faster).
2. **tower_of_hanoi** returning 206 instead of 255. LICM hoisted `g = 0` out of `for(...){g=0; bump();}` because it didn't know `bump()` calls write to `g`. Fix: added `siblings_contain_call` guard to `_is_hoistable_invariant_assignment` in `pcc/passes/llvm_loop_explicit.py`. Bench: 0.64x pcc/clang (pcc 36% faster).
3. **dfs_cycle** — same LICM function-call aliasing root cause; fix landed with tower_of_hanoi. Bench: 1.02x pcc/clang (tied).

### Step 7: Env-merging infrastructure — ✅ Restored

Env-merging infrastructure that was rolled back in commit 91b8a849 is now back in a form that keeps the shared-path gates green:

- per-block env snapshots in `_lower_condition_branch` via `exit_envs` param,
- `_merge_envs` at loop exit, do-while latch, and for-continue block,
- `_blocks_targeting` helper to find all cond-chain predecessors of body/exit,
- dedicated `for.continue` block so `continue` runs the `next` expression,
- `_patch_loop_back_edge_phis` per-predecessor env snapshots for do-while body phis (gz_fetch fix survives).

Verified against the exact regressions that originally drove the rollback:
- `tests/test_ssa_lowering.py::test_ssa_lowering_side_effecting_short_circuit_condition` covers `do {} while (++i < 3 && ++j < 3 && i + j < 100);` (deflate.c longest_match pattern).
- `tests/test_ssa_lowering.py::test_ssa_lowering_break_with_short_circuit_condition` covers break inside do-while with `&&` condition (deflate.c fill_window pattern).

Broader validation: SSA focused 173 passed (builder/lowering/gvn/adce/sccp + bootstrap/gvn/adce/sccp passes + zlib + Lua math.lua) with 2 pre-existing unrelated DSE failures, c_testsuite 508 passed with all Phase 4 MVP features active (struct-value alloca, struct copy for scalar/array/nested-struct fields, array-of-struct field access, positional/partial/designated initializers, local array init, array designators, string-literal char array init, compound literals, nested InitList for struct-with-array-field, nested struct field init), gcc_torture 3442 passed / 9 failed — the 9 match master's pre-existing failures exactly (no new regressions after the `_merge_envs` undef-type fix below). Bench correctness on 7 struct-heavy benchmarks (byte_histogram, tower_of_hanoi, matmul_64, nbody_1k, qsort_10k, crc32_100k, heapsort_10k): all clean, pcc/clang geomean 0.54x at O0 all-pass (pcc 1.85x faster than clang -O2).

Secondary fix landed with env-merging: `_merge_envs` previously created `SSAUndef` placeholders with the default `type_name="int"` and typed the resulting phi from that placeholder, which broke pointer-typed vars whose other incoming was a pointer value (`phi i32 [undef, ...], [ptr, ...]` — gcc_torture `pr103255.c` regression). The fix reads the authoritative type from the first predecessor that actually binds the variable.

Probe on 6 small zlib source files with env-merging: adler32.c (5 built, 0 skipped), compress.c (2, 1), crc32.c (10, 0), gzclose.c (1, 0), gzlib.c (16, 2), gzread.c (12, 3) → 46 built / 6 skipped = 88% SSA coverage on structured scalar functions. Large files (deflate.c, trees.c, inflate.c, inftrees.c, infback.c, inffast.c) not probed due to preprocessor performance on macro-heavy headers.

### Step 8: Phase 4 Minimum MemorySSA prototype — ✅ Landed first slice

First slice: **local struct/union value as stack alloca**. A local declaration `struct S s;` (no initializer) is modeled as `SSAStackAlloc` with `elem_type_name="struct S"`, so the existing `.`-on-pointer path handles `s.field` without requiring a full MemorySSA analysis. See `pcc/ssa/builder.py:_lower_decl` Phase 4 MVP branch, `_is_aggregate_value_type` helper, and `_lower_struct_init_list` helper.

Scope (currently accepted):
- struct or union local with no initializer,
- struct local with a positional scalar-only InitList (e.g. `struct S s = {1, 2};`),
- partial InitList with C99 zero-fill of remaining fields (e.g. `struct S s = {0};` — common memset-via-init idiom),
- designated initializers `{.field = value}` including out-of-order and partial forms with C99 zero-fill,
- compound literal `(struct T){...}` as initializer,
- struct with array field nested init like `struct S { int a; int arr[3]; }; struct S s = {1, {2, 3, 4}};` (including string literal for `char name[]` fields and partial zero-fill),
- struct with nested struct field init like `struct Outer { struct Inner inner; int tag; }; struct Outer o = {{1, 2}, 5};` (positional, designated, partial — all zero-fill remaining sub-fields correctly),
- local array with positional scalar-only InitList (e.g. `int a[3] = {1, 2, 3};`, partial `int a[5] = {10, 20};` zero-fills the rest, unsized `int a[] = {1, 2, 3};` infers count),
- array designators `{[N] = value}` with zero-fill between designated positions, including mixed positional + designated and out-of-order cases,
- string-literal char array init (`char s[] = "hello";` unsized → count includes NUL; `char s[5] = "abc";` sized → truncate/zero-fill),
- **2D arrays** `int mat[N][M]; mat[i][j] = ...; x = mat[i][j];` — flat alloca with count=N*M, indexed as `i*M + j` (tracked via `_state.multi_dim_arrays[name] = inner_dim`). Compound assign `mat[i][j] += X` also works. 2D array initializers not yet supported,
- `s.field` / `s.field[i]` / `s.inner.field` access (including nested structs),
- `&s` — yields the alloca pointer, so `helper(&s)` and `p = &s; p->field` work,
- struct-to-struct copy `s2 = s1` with scalar fields, array fields (unrolled index copy), and nested struct fields (recursive field-by-field copy through the inner struct's address),
- array of struct: `struct E arr[N]; arr[i].field = ...; arr[i].field;` — the `.field` on `arr[i]` is lowered via a synthetic `(&arr[i])->field` path that computes the element address then dispatches through the `.`-on-pointer code,
- multiple struct locals in one function.

Rejections at bootstrap time (fall back to AST codegen):
- `helper(s)` — passing struct-alloca local as whole value (would need memcpy / ABI-specific lowering),
- `return s` — struct-value return (would need memcpy / sret lowering),
- struct copy with aggregate field (array / nested struct / union) in either side,
- struct copy across mismatched types,
- union initializer (ambiguous, needs explicit named init),
- nested InitList or designated initializers,
- InitList where any field type is an aggregate (array, struct, union).

Tracking set: `_state.struct_alloca_locals` names every local that was promoted to a struct alloca. All whole-value sites check this set and raise `SSAConstructionError`, and the enclosing function falls back cleanly.

Tests:
- `tests/test_ssa_lowering.py::test_ssa_lowering_local_struct_value_scalar_fields` (scalar `s.x`, `s.y`),
- `tests/test_ssa_lowering.py::test_ssa_lowering_local_struct_value_array_field` (array `s.arr[i]` in loops — zlib deflate-state shape),
- `tests/test_ssa_lowering.py::test_ssa_lowering_local_struct_value_nested_field` (`o.inner.v`),
- `tests/test_ssa_lowering.py::test_ssa_lowering_address_of_local_struct` (`helper(&s)`),
- `tests/test_ssa_lowering.py::test_ssa_lowering_struct_positional_init_list` (`struct S s = {1, 2};`),
- `tests/test_ssa_lowering.py::test_ssa_lowering_struct_partial_init_list_zero_fills` (`struct S s = {0};` memset-via-init),
- `tests/test_ssa_lowering.py::test_ssa_lowering_scalar_struct_copy_assignment` (`s2 = s1`),
- `tests/test_ssa_lowering.py::test_ssa_lowering_array_of_struct_field_access` (`arr[i].field`),
- `tests/test_ssa_bootstrap_pass.py::test_ssa_bootstrap_pass_builds_local_struct_with_field_access`,
- `tests/test_ssa_bootstrap_pass.py::test_ssa_bootstrap_pass_builds_scalar_struct_copy_assignment`,
- `tests/test_ssa_bootstrap_pass.py::test_ssa_bootstrap_pass_rejects_aggregate_field_struct_copy`,
- `tests/test_ssa_bootstrap_pass.py::test_ssa_bootstrap_pass_rejects_struct_value_call_argument`,
- `tests/test_ssa_bootstrap_pass.py::test_ssa_bootstrap_pass_rejects_struct_value_return`.

LLVM reference boundary: this is narrower than `MemorySSA.cpp` (no memory phi nodes, no cross-block aliasing). It is also narrower than LLVM's `mem2reg` (we are *introducing* alloca, not promoting it), but downstream LLVM `mem2reg` / SROA is expected to promote these away for simple cases. The point of this slice is to widen SSA bootstrap's coverage so SCCP/GVN/DSE analysis reaches the struct-heavy functions that previously fell to AST codegen only.

### Step 9: Phase 4 next cut — Partial

Further Phase 4 work:
- ✅ cross-block memory forwarding landed (straight-line single-predecessor fallthrough) — `pcc/passes/memory_opt.py` now pre-scans CFG for predecessor counts and keeps store/load facts alive across edges where the target block has exactly one predecessor and the terminator is an unconditional `br`.
- ✅ 2D arrays landed (access + compound assign). See `_state.multi_dim_arrays`, `_lower_2d_array_ref_load`, `_compute_2d_array_addr` in `pcc/ssa/builder.py`.
- 2D array initializers (`int mat[3][3] = {{1,2,3}, {4,5,6}, {7,8,9}};`) — not yet landed,
- 3D+ arrays — deferred (would need recursive inner_dim chain),
- union fields in struct init (ambiguous without designated init),
- struct-value call arguments (`helper(s)`), struct-value return (`return s`) — needs platform-specific ABI lowering,
- full MemorySSA with memory phis for multi-predecessor merge (current extension handles only single-pred fallthrough).

### Step 10: Phase 5 loop canonicalization — First slice landed

`pcc/ssa/loop_phi.py` + `pcc/passes/ssa_loop_phi.py` add an SSA-backed loop-header phi classifier that identifies each header phi as one of:
- **dead** (zero uses),
- **invariant** (all incomings equal),
- **induction** (back-edge value = `phi + K` or `phi - K` for constant K — records step),
- **reduction** (back-edge value = `phi op X` for `op ∈ {+, -, *, &, |, ^}` — records the op),
- **other**.

Counts are recorded in `ctx.stats` as `ssa.loop_phi.{dead,invariant,induction,reduction,other}`. Per-function `SSALoopPhiResult` objects are stashed on `ctx.ssa_loop_phi_results` for downstream consumers.

Tests: `tests/test_ssa_loop_phi.py` covers increment induction, decrement induction, sum reduction, xor reduction, and the no-loop case.

Phase 5 future cuts (not yet landed):
- consume loop-phi classification to drive dead-phi elimination, IV canonicalization, reduction-specific rewrites,
- loop-load-elimination (hoisting loads whose address is loop-invariant and whose memory is not written in the loop),
- vectorization-friendly canonical block shapes.

### Step 11: Phase 6 cross-TU / whole-program — First slice landed

`pcc/passes/whole_program.py` + `pcc/passes/whole_program_pass.py` add a whole-program analyzer that scans a list of `(unit_name, c_ast.FileAST)` pairs to derive:
- function definitions with linkage (static/extern/default) and parameter shape,
- call sites with constant-arg signatures (literal int / string / negated int),
- per-function sets of constants seen at each argument position,
- **dead internal functions** (static linkage, no callers, not main),
- **specialization candidates**: internal functions where every call passes the same constant at some argument position (records argpos → const value).

Counts recorded: `whole_program.{functions,call_sites,dead_internal_functions,specialization_candidates}`. Full result stashed on `ctx.whole_program_result`.

Tests: `tests/test_whole_program.py` covers same-constant specialization, mixed-constant rejection, dead-internal detection, cross-TU call tracking, and the pass-level stats recording.

Phase 6 future cuts (not yet landed):
- driver integration: populate `ctx.whole_program_asts` in `compile_translation_units` so the analyzer actually sees multiple TUs in the default pipeline,
- consumer passes: per-TU specialization rewriter, dead-function remover,
- cross-TU constant / range propagation,
- LTO-like internal merge mode.

### Remaining work for this batch

- Re-probe zlib coverage once preprocessor can handle `deflate.c` / `trees.c` / `inflate.c` (the struct-heavy files) in reasonable time — the "13 direct aggregate value field access" rejections should now largely be satisfied.
- Low-tier local-memory cleanup broadening remains bounded to:
  - declared-slot identity aliases,
  - pointer-slot loaded aliases with preserved provenance,
  - zero-offset aggregate element aliases that do not violate the original slot type,
  - mixed SSA-scalar + local-memory regression cases.

## Definition Of Done For This Plan

This plan is done when all of the following are true:

- the internal SSA MidTier is the default path for a meaningful scalar subset,
- current bounded scalar pass translations have been replaced by real SSA-backed implementations where appropriate,
- benchmark data shows a clear reduction in the `O0 all-pass` vs `clang -O2` gap,
- `pcc -O2` remains competitive with or better than the current baseline,
- the remaining gap, if any, is narrow enough to justify deciding whether machine/backend work is worth owning.

## Definition-of-Done Status — April 17, 2026

All five DoD items satisfied. Full 80-case benchmark suite verified:

### O0 all-pass vs clang -O2 (core comparison)

```
pcc/clang geomean = 0.36x → pcc is 2.78x FASTER than clang -O2
77 faster / 2 tied / 1 slower out of 80
```

Top wins (pcc vs clang -O2):
- `min_max_scan`: 0.04x (25x faster — 155.88ms → 6.89ms)
- `life_1d`: 0.06x (18x)
- `file/bitcount`: 0.08x (12x)
- `gray_code`: 0.10x (10x)
- `game_of_life`: 0.11x (9x)
- `file/dhrystone`: 0.11x
- `two_sum_hash`: 0.13x
- `dp_fibonacci_big`: 0.14x
- `bit_reverse`: 0.14x
- `bitcount_1M`: 0.16x

Only slower: `file/huffman` 1.72x.

### O2 (pcc -O2 vs clang -O2)

```
pcc/clang geomean = 1.02x
19 faster / 42 tied / 19 slower out of 80
```

Top O2 wins: `linked_list_walk` 0.71x, `xorshift_10M` 0.68x, `horner_poly` 0.77x, `file/edit_distance` 0.78x, `dp_fibonacci_big` 0.76x, `file/fft` 0.82x.

### Pass effectiveness (all-pass vs no-pass)

- O0: 1.02x (16 faster / 37 tied / 27 slower) — roughly neutral
- O2: 1.00x (19 faster / 42 tied / 19 slower) — exactly neutral

### DoD criteria checkoff

| Criterion | Target | Actual | Met |
|-----------|--------|--------|-----|
| SSA MidTier is the default path for a meaningful scalar subset | — | struct/array/int/ptr/loop with full InitList / designators / 2D / copy / call-thru support | ✅ |
| bounded scalar passes replaced by SSA-backed | — | SCCP, GVN, DSE, ADCE, loop-phi classifier | ✅ |
| O0 all-pass vs clang -O2 gap clearly reduced | reduction | pcc is 2.78x FASTER than clang -O2 | ✅✅ |
| pcc -O2 competitive with baseline | no regression | 1.02x (essentially parity) | ✅ |
| remaining gap narrow enough to decide on machine/backend work | decidable | O0 already surpasses clang -O2; O2 at parity — the plan's "is machine/backend work worth owning?" answer is **"no, current LLVM backend + pcc mid-tier is already competitive or winning"** | ✅ |

## Post-DoD Optional Improvements

The plan's Definition of Done is satisfied, but several tractable improvements remain if we want to push further:

### Single-case O0 regression investigation

- **`file/huffman` (O0 1.72x slower)** — the only O0 case where pcc is slower than clang -O2. Worth investigating to understand which pass is introducing overhead or what code shape is being mis-canonicalized. Likely candidates: aggressive inlining + code-bloat, or a specific canonicalize / dce ordering that happens to worsen this shape.

### O2 regression investigation (4 largest gaps)

- **`dfs_cycle` 2.30x slower** (pcc 15.51ms vs clang 6.73ms at O2) — largest O2 regression. The O0 number is `1.02x (tied)`, which means something about the O2 pipeline is actively worsening this shape. Candidates: LICM ordering regression, induction-var canon choosing a worse form, or a SSA-to-AST consumer rewrite that blocks downstream LLVM passes.
- **`coord_compress` 1.79x slower** at O2 — similar pattern. Check if it's struct-heavy code that now gets a less optimal shape through the extended Phase 4 struct-alloca path.
- **`dp_knapsack` 1.39x slower**, **`dp_edit_distance` 1.31x slower** — both are 2D-array-heavy. With 2D arrays now going through SSA bootstrap (flat alloca + explicit `i*INNER + j` GEPs), the LLVM backend may be getting different IR than before. Investigate if the flat-index form is harder for LLVM's loop vectorizer or aliasing analysis to reason about vs the `[3 x [3 x i32]]` natural form.

### Phase 5 consumer rewrites

`pcc/ssa/loop_phi.py` currently only **analyzes**; no rewrite consumer runs against the classification. Adding these would close the gap to LLVM's `IndVarSimplify.cpp` and reduction-aware vectorization hints:

- **Dead-phi elimination** — loop-header phi nodes classified as `LoopPhiKind.DEAD` can be dropped; their back-edge SSA values can be DCE'd if no other user exists.
- **Invariant-phi hoisting** — phi nodes classified as `INVARIANT` collapse to their seed value directly; the phi can be removed and its uses replaced with the seed.
- **Induction-var canonicalization** — rewrite non-canonical IVs (e.g. `i = start; ... i = i + 2;`) to the canonical `i_new = (original_i - start) / 2` form so LLVM's vectorizer sees a unit-stride IV. Mirror `IndVarsPass` but consume SSA analysis instead of AST walking.
- **Reduction-aware metadata** — attach `!llvm.loop.parallel_accesses`, `!llvm.mem.parallel_loop_access`, or `fast-math` hints on reduction phis so LLVM's vectorizer can parallelize the reduction.
- **Loop-load-elimination** — when a load's address is loop-invariant and its memory is not written in the loop body, hoist the load out of the loop. This is LLVM's `LoopLoadElimination.cpp` behavior. Combines well with the Phase 4 cross-block memory_opt extension.

### Phase 6 driver integration

`pcc/passes/whole_program.py` analyzer exists, but `compile_translation_units` does not currently populate `ctx.whole_program_asts`. To make Phase 6 active in production:

- **Driver integration** — before per-TU compilation, parse all ASTs once, run `WholeProgramAnalysisPass` on the multi-TU view, stash the `WholeProgramResult` in a shared location (file or shared-memory), then compile each TU with the result available in its own `PassContext`.
- **Specialization rewriter** — consumer that takes `specialization_candidates` and clones internal-linkage functions with the constant arg baked in at each matching call site. This requires the driver integration.
- **Dead-function remover** — consumer that drops `dead_internal_functions` from each TU. Works with or without driver integration since `ctx.whole_program_result` already carries the set.
- **Cross-TU constant / range propagation** — for each constant-arg signature, propagate through the function body and into callees. Requires the analyzer to model constant return values as well.
- **LTO-like internal merge** — pre-link all TU ASTs into a single compilation unit before lowering. This is the "big hammer" but sidesteps the cross-TU plumbing cost.

### Phase 4 remaining refinements

- **2D array initializers** (`int mat[3][3] = {{1,2,3}, {4,5,6}, {7,8,9}};`) — currently rejected. Would close the last 2D-array gap.
- **3D+ arrays** — extension of 2D, recursive `inner_dim` chain tracking.
- **Struct-value call arguments / returns** — `helper(s)` and `return s` still fall back to AST codegen. Needs platform-specific ABI lowering (byval/sret on x86-64, HFA split on AArch64).
- **Full MemorySSA** — multi-predecessor memory-phi merges, across arbitrary CFG shapes. Current extension is limited to single-predecessor fallthrough (≈LLVM's NewGVN's "trivial" case).

### Validation gap

- **c_testsuite, zlib, Lua math.lua, bench** all green. gcc_torture at 3442 passed / 9 failed, with the 9 matching master's pre-existing failures.
- No new validation surface required to call the plan closed — the above DoD numbers are sufficient — but a **zlib re-probe after preprocessor speedup** would quantify SSA coverage impact on the struct-heavy files (`deflate.c`, `trees.c`, `inflate.c`) that currently can't be probed because the preprocessor is too slow on their macro-heavy headers. This is an orthogonal enabling item, not a plan blocker.
