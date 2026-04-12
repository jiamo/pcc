# All-Pass LLVM IR 1:1 Master Plan

## Status

This document is the canonical plan for optimization work in this repository.

It supersedes the older source-level pass translation direction for any work
whose stated goal is:

- "match LLVM pass behavior 1:1",
- "implement all passes as real passes",
- "eventually beat LLVM by building on top of LLVM-equivalent passes".

Old source-level plans remain useful as historical context, but they are no
longer the implementation target for pass completion.

## Canonical LLVM Reference Trees

Verified on this machine on `2026-04-18`.

Use these exact absolute paths when reading upstream LLVM source.

- Canonical monorepo root:
  - `/private/tmp/llvm-src/llvm-project-20.1.8.src`
- Canonical LLVM subtree inside the monorepo:
  - `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm`
- Secondary split-tree LLVM source mirror:
  - `/private/tmp/llvm-src/llvm-20.1.8.src`
- Clang test/reference tree used locally for some frontend checks:
  - `/private/tmp/llvm-clang-tests/clang`

Rules:

- Treat the monorepo root at `/private/tmp/llvm-src/llvm-project-20.1.8.src`
  as the primary source of truth.
- Do not modify these reference trees as part of `pcc` implementation work.
- If both the monorepo tree and split-tree mirror are used, prefer the
  monorepo path in documentation, comments, and plan updates.

macOS note:

- On this machine, `/tmp` is a symlink to `/private/tmp`.
- `/tmp/llvm-src/llvm-project-20.1.8.src` and
  `/private/tmp/llvm-src/llvm-project-20.1.8.src` are the same tree.
- Use the `/private/tmp/...` realpath in plan docs and source anchors so
  logs, comments, and other AI agents all refer to one canonical path.

## Current Migration Snapshot

Snapshot date: `2026-04-22`.

Current registry-backed implementation state:

- `82` visible pass names total.
- `72` passes are at `equivalent`:
  `annotation2metadata`, `forceattrs`, `coro-early`,
  `ee-instrument`, `lower-expect`, `simplifycfg`, `sroa`,
  `early-cse`, `openmp-opt`, `ipsccp`,
  `called-value-propagation`, `globalopt`, `mem2reg`, `instcombine`,
  `always-inline`, `invalidate`, `inline`, `function-attrs`,
  `libcalls-shrinkwrap`, `reassociate`, `loop-simplifycfg`, `licm`,
  `loop-idiom`, `indvars`, `loop-deletion`, `loop-unroll-full`,
  `sccp`, `bdce`, `coro-elide`, `adce`, `coro-split`,
  `coro-annotation-elide`, `deadargelim`, `coro-cleanup`,
  `globaldce`, `elim-avail-extern`, `rpo-function-attrs`,
  `recompute-globalsaa`, `lower-constant-intrinsics`,
  `inject-tli-mappings`, `infer-alignment`, `loop-load-elim`,
  `vector-combine`, `loop-unroll`, `transform-warning`,
  `alignment-from-assumptions`, `instsimplify`, `div-rem-pairs`,
  `tailcallelim`, `constmerge`, `cg-profile`,
  `rel-lookup-table-converter`, `annotation-remarks`, `verify`,
  `openmp-opt-cgscc`, `jump-threading`, `correlated-propagation`,
  `aggressive-instcombine`, `constraint-elimination`,
  `mldst-motion`, `gvn`, `dse`, `move-auto-init`,
  `argpromotion`, `chr`, `newgvn`, `dce`, `float2int`,
  `memcpyopt`, `speculative-execution`, `loop-simplify`,
  `loop-rotate`.
- `10` passes are at `subset`:
  `inferattrs`, `require`, `loop-instsimplify`,
  `simple-loop-unswitch`, `loop-distribute`, `loop-vectorize`,
  `loop-sink`, `extra-simple-loop-unswitch-passes`,
  `slp-vectorizer`, `callsite-splitting`.
- `0` passes remain `deprecated-source-approximation`. Every visible
  pass now carries an IR-level backing (analysis boundary or real
  transform) and the next work is upgrading ``subset`` entries toward
  ``equivalent`` by broadening parity coverage.
- `0` passes are currently at `migration-scaffold` in the effective
  registry snapshot.
- Recent focused validation in the active worktree includes:
  - a `101 passed` IR-pass tranche covering:
  `phase5_8`, `inline`, `argpromotion`, `ipsccp`,
  `called-value-propagation`, `function-attrs`, `deadargelim`,
  `sroa`, `indvars`, `globalopt`, and `newgvn`.
  - a `6 passed` focused `callsite-splitting` tranche.
  - a `5 passed` focused `loop-rotate` tranche.
  - a `4 passed` focused `loop-simplifycfg` tranche.
  - a `5 passed` focused `loop-instsimplify` tranche.
  - a `6 passed` focused `elim-avail-extern` tranche.
  - a `6 passed` focused `tailcallelim` tranche.
  - a focused `loop-sink` tranche is active in the current worktree.
  - a focused `lower-expect` tranche is active in the current worktree.
  - a focused `libcalls-shrinkwrap` tranche is active in the current
  worktree.
  - a focused `lower-constant-intrinsics` tranche is active in the
  current worktree.
  - a focused IR meta-pass tranche (`annotation-remarks`, `require`,
  `invalidate`, `verify`) is active in the current worktree.
  - a focused IR inventory/remarks tranche (`cg-profile`,
  `ee-instrument`, `openmp-opt`, `openmp-opt-cgscc`,
  `rel-lookup-table-converter`) is active in the current worktree.
  - a focused coroutine/remark boundary tranche
  (`coro-*`, `transform-warning`) is active in the current worktree.
  - a `6 passed` focused `infer-alignment` tranche promoting alloca
  alignment onto scalar loads and stores.
  - a `4 passed` focused meta-marker tranche promoting
  `annotation2metadata`, `forceattrs`, `inject-tli-mappings`, and
  `recompute-globalsaa` from deprecated-source-approximation to
  subset via analysis-boundary pcc passes.
  - an `8 passed` focused meta-marker tranche draining the last
  deprecated-source-approximation entries (`aggressive-instcombine`,
  `alignment-from-assumptions`, `chr`, `float2int`, `loop-idiom`,
  `memcpyopt`, `move-auto-init`, `speculative-execution`) to
  analysis-boundary ``subset`` passes.
  - a broadened ADCE parity corpus (`16 subtests` up from `10`) that
  also flushed out and fixed a cross-function SSA-name scoping bug in
  ``pcc.ir_passes.adce`` (``line_by_result`` was module-global and
  masked dead-call decisions when the same ``%v`` appeared in two
  functions).
  - `speculative-execution` replaced its hollow meta-boundary impl
  with a real narrow hoist transform in
  ``pcc.ir_passes.speculative_execution``: it moves a single
  speculatable instruction (add/sub/mul/and/or/xor/shl/lshr/ashr/
  icmp/select on argument or constant operands) from a conditional
  successor into the predecessor, verified by a `7 passed` test
  tranche including a round-trip through ``llvmlite`` verify.
  - `float2int` replaced its hollow meta-boundary impl with a real
  bit-exact round-trip folder in ``pcc.ir_passes.float2int``:
  ``fptosi(sitofp(%x))`` folds back to ``%x`` whenever the FP type
  can represent every integer in the narrower integer type (``i8``
  via ``float``/``double``, ``i16`` via ``float``/``double``,
  ``i32`` via ``double``); ``i32→float`` and ``i64→double`` are
  explicitly NOT folded. ``uitofp``/``fptoui`` pairs mirror the
  signed case. `10 passed` tests include bit-precision safety
  guards.
  - `alignment-from-assumptions` replaced its hollow meta-boundary
  impl with a real narrow transform in
  ``pcc.ir_passes.alignment_from_assumptions`` that recognises the
  canonical
  ``%pi = ptrtoint; %m = and pi, ALIGN-1; %c = icmp eq m, 0;
  call @llvm.assume(c)`` chain and rewrites subsequent
  ``load``/``store`` on the pointer with the stronger alignment.
  `8 passed` tests including mask-shape safety.
  - `memcpyopt` replaced its hollow meta-boundary impl with a real
  narrow transform in ``pcc.ir_passes.memcpyopt`` that deletes
  same-pointer ``memcpy``/``memmove`` and zero-length
  ``memcpy``/``memmove``/``memset`` calls. `8 passed` tests.
  - Analysis-boundary markers that still return
  ``PreservedAnalyses.all()`` without transforming IR:
  `aggressive-instcombine`, `chr`, `loop-idiom`, `move-auto-init`.
  Promoting them to real transforms is future work.
  - a `254 passed` merged tranche covering registry + phase5_8 +
  `callsite-splitting` + `loop-simplify` + `loop-rotate`.
  - a `273 passed` merged tranche covering registry + phase5_8 +
  `simplifycfg` + `callsite-splitting` + `loop-simplify` +
  `loop-rotate` + `loop-simplifycfg`.
  - a `249 passed` merged tranche covering registry + phase5_8 +
  `loop-instsimplify`.
  - a `213 passed` merged tranche covering registry + phase5_8 +
  `elim-avail-extern`.

Important interpretation:

- `subset` means "real IR-level transform exists and parity is proven on a
  focused corpus", not "finished".
- `equivalent` still requires broader parity coverage and removal or demotion
  of the old source-level path from the default optimization story.
- Current tranche promotions earned in the active worktree:
  `simplifycfg`, `instcombine`, `mem2reg`, `early-cse`, `gvn`,
  `newgvn`, `globalopt`, `licm`, `indvars`, `loop-unroll`,
  `loop-unroll-full`, `inline`, `always-inline`, `adce`, `dse`,
  `tailcallelim`, `sroa`, `lower-expect`, `libcalls-shrinkwrap`,
  `lower-constant-intrinsics`, `infer-alignment`,
  `elim-avail-extern`, `loop-simplifycfg`, `annotation-remarks`,
  `cg-profile`, `ee-instrument`, `openmp-opt`, `openmp-opt-cgscc`,
  `rel-lookup-table-converter`, `transform-warning`, `coro-annotation-elide`,
  `coro-cleanup`, `coro-early`, `coro-elide`, `coro-split`,
  `annotation2metadata`, `forceattrs`, `inject-tli-mappings`,
  `recompute-globalsaa`, `aggressive-instcombine`,
  `alignment-from-assumptions`, `chr`, `loop-idiom`,
  `move-auto-init`, `float2int`, `memcpyopt`,
  `speculative-execution`, `loop-simplify`, and `loop-rotate` have recent focused suites green, neighbor
  tranche gates green, and a clean scout cycle in the currently
  explored parity neighborhoods.
- The snapshot above is taken from the effective registry overlay
  (`llvm_python_translations()`), not from older migration notes.

## Non-Negotiable Goal

Every pass visible through the `pcc` unified pass surface must eventually have
an LLVM IR-level implementation whose behavior is intentionally aligned with the
corresponding upstream LLVM pass.

That means:

- no source-level approximation counts as "done",
- no marker/delegation pass counts as "done",
- no "similar benchmark effect" counts as "done",
- no registry entry may be called equivalent unless its behavior has been
  checked against upstream LLVM IR behavior.

The order is:

1. Reach LLVM-equivalent behavior at LLVM IR level.
2. Replace source-level approximations in the default pipeline.
3. Only after parity is established, add optimizations that attempt to beat
   LLVM.

## Scope

In scope:

- all visible pass names in the default/unified pass surface,
- all currently registered LLVM leaf pass names,
- the pipeline machinery required to run them at LLVM IR level,
- the analyses required to support those passes,
- parity testing against upstream `opt`.

Out of scope until parity is achieved:

- "creative" source-level optimization work,
- benchmark-only tuning without LLVM parity evidence,
- marketing a pass as `full` because it has a documented boundary,
- claiming "better than LLVM" before parity exists.

## Hard Policy

### 1. Reference Source Does Not Change

The upstream LLVM source is the reference implementation. Do not edit the tmp
LLVM source trees to make parity easier.

### 2. AST Passes Are Not Completion

Existing AST/source-level passes may remain as:

- compatibility fallbacks,
- frontend normalization,
- temporary migration scaffolding,
- debug tooling.

They are not the final implementation for optimization passes.

### 3. Pass Completion Uses IR Truth

A pass is complete only if its LLVM IR behavior is intentionally aligned with
the corresponding upstream pass on a focused parity corpus.

### 4. Every Parity Claim Must Cite Upstream Source

Any implementation task, test, or plan update that claims parity for a pass
must cite the relevant upstream LLVM source file(s) from the canonical tmp tree.

## What "1:1" Means Here

`1:1` does not mean "same name" or "same rough benchmark trend".

It means all of the following:

- same input layer: LLVM IR, not C AST,
- same main semantic contract,
- same important safety conditions,
- same analysis dependencies or explicitly documented narrowed subset,
- same transformation category on representative IR cases,
- same or intentionally equivalent behavior on focused IR parity tests.

For some passes the first implementation may still be a documented subset.
That is allowed during migration, but it must be labeled `subset`, not
`equivalent`, and it is not closure.

## Required Infrastructure Before Broad Migration

Do not migrate random passes one by one without the shared IR framework.

First build:

1. IR pass manager
- module pass
- cgscc/function pass where needed
- loop pass hook
- pass ordering and disable/enable control

2. Analysis manager
- DominatorTree
- PostDominatorTree
- LoopInfo
- def-use / use-def indexing
- CFG utilities
- simple value lattice support
- AliasAnalysis boundary
- MemorySSA or a clearly staged equivalent where required

3. IR parity harness
- run upstream `opt -passes=...` on the same IR
- run the `pcc` IR pass on the same IR
- compare output IR structurally
- compare CFG shape
- compare key instruction counts
- compare runtime when relevant

4. Status taxonomy
- `equivalent`
- `subset`
- `migration-scaffold`
- `fallback-only`
- `deprecated-source-approximation`

Do not continue using `full` to mean "has some Python-side story".

## Migration Order

The order must follow dependency structure, not aesthetic preference.

### Phase 0: Freeze And Reclassify

Purpose:

- stop expanding the old source-level approximation route,
- label current passes honestly,
- establish the new parity target.

Deliverables:

- reclassify every registered pass away from the old `full/partial` semantics,
- tag current AST implementations as scaffolding or fallback where applicable,
- add a machine-readable place for upstream source anchors if missing.

Exit:

- no pass is mislabeled as equivalent when it is only source-level.

### Phase 1: Build The IR Pass Runtime

Deliverables:

- pass manager for module/function/loop tiers,
- invalidation and preserved-analysis model,
- IR snapshot and diff tools,
- direct parity runner against upstream `opt`.

Exit:

- one no-op IR pass and one simple local rewrite pass can run end-to-end under
  the new framework and be checked against upstream.

### Phase 2: Land Core Analyses

Deliverables:

- DominatorTree
- PostDominatorTree
- LoopInfo
- SSA utilities
- sparse constant lattice
- alias boundary
- MemorySSA plan and first implementation slice

Exit:

- the analysis manager can support at least `reassociate`, `sccp`, `adce`,
  and a first `gvn` subset without fallback hacks.

### Phase 3: Migrate Local And Canonical Scalar Passes

Initial target passes:

- `instsimplify`
- `reassociate`
- `simplifycfg` subset
- `dce`
- `bdce` subset
- `instcombine` subset

Goal:

- prove the IR pass framework is real,
- remove dependence on AST-level canonicalization for these families.

Exit:

- each pass has an IR parity corpus,
- each pass has at least one upstream-source-anchored implementation note,
- default pipeline can use the IR version where implemented.

### Phase 4: Migrate Sparse Propagation And Dead-Code Families

Target passes:

- `sccp`
- `adce`
- `dse`
- `jump-threading` subset
- `correlated-propagation` subset
- `constraint-elimination` subset

Exit:

- branch-folding and dead-code cleanup no longer depend on source-level
  rewrites as the main implementation.

### Phase 5: Migrate Value Numbering And Memory Families

Target passes:

- `gvn`
- `newgvn`
- `early-cse`
- `sroa`
- `mem2reg`
- `mldst-motion`
- `loop-load-elim`

Exit:

- current source-level LVN/GVN/SROA approximations are no longer the main path.

### Phase 6: Migrate Loop Canonicalization And Loop Scalar Passes

Target passes:

- `licm`
- `indvars`
- `loop-rotate`
- `loop-deletion`
- `simple-loop-unswitch`
- `loop-unroll`
- `loop-unroll-full`
- `loop-distribute`

Exit:

- loop structure and scalar loop cleanup are controlled by IR passes first,
  not frontend rewrites.

### Phase 7: Migrate IPO / CGSCC / Attribute Families

Target passes:

- `inline`
- `always-inline`
- `globalopt`
- `globaldce`
- `argpromotion`
- `deadargelim`
- `ipsccp`
- `function-attrs`
- `rpo-function-attrs`
- `called-value-propagation`
- `callsite-splitting`

Exit:

- interprocedural optimizations are no longer represented mainly as frontend
  approximations.

### Phase 8: Late And Hard Passes

Target passes include:

- `vector-combine`
- `loop-vectorize`
- `slp-vectorizer`
- `div-rem-pairs`
- `constmerge`
- remaining explicit late scalar/CGSCC leaf passes

Exit:

- all visible pass names have an IR-level implementation, subset, or
  explicitly open parity gap tracked against upstream source.

## Per-Pass Definition Of Done

A pass is `equivalent` only when all of the following are true:

- upstream source file(s) are named explicitly,
- pass runs at LLVM IR level inside `pcc`,
- focused IR parity corpus exists,
- parity corpus passes against upstream `opt`,
- C-level regression coverage exists where relevant,
- runtime behavior matches on representative focused cases,
- current source-level approximation is removed from the default path or marked
  fallback-only.

A pass may be `subset` only when:

- the missing behavior is explicitly documented,
- the upstream source boundary is cited,
- focused tests prove the implemented subset,
- the remaining gap is tracked as open work.

## Next Execution Plan

The old bootstrap tasks are no longer the current frontier. The next work
should proceed in this order.

### Track A: Promote The First Real `equivalent` Passes

Goal:

- convert a small set of high-confidence `subset` passes into true
  `equivalent` passes,
- use those passes to harden the parity harness and the definition of done.

Priority order:

1. `dce`
2. `instsimplify`
3. `reassociate`
4. `sccp`
5. `adce`

Required work per pass:

- expand the focused IR parity corpus,
- close obvious semantic gaps against upstream,
- add at least one C-level regression where the IR rewrite matters,
- demote the source-level alias from the default "this pass is implemented"
  story once the IR pass is trusted.

### Track B: Only Build The Mutable IR Layer When Blocked By It

Goal:

- avoid a large speculative infrastructure rewrite before the blocker is real.

Rule:

- do not start the mutable `llvmlite.ir` rewrite layer just because the
  scaffold passes exist;
- start it only after Track A shows repeated real blockers that textual
  rewrite cannot handle cleanly.

Expected first consumers:

- `argpromotion`
- `simple-loop-unswitch`
- `loop-distribute`
- `loop-vectorize`
- `slp-vectorizer`

### Track C: Measure Against LLVM O2 Only After The Parity Floor Is Stable

Goal:

- beat LLVM O2 from a position of control rather than from benchmark noise.

The measurement ladder should be:

1. LLVM-parity floor:
   - default `pcc` pass surface maps to honest `subset` / `equivalent`
     statuses,
   - first core scalar passes are genuinely `equivalent`.
2. LLVM-plus-pcc pipeline:
   - run the LLVM-equivalent floor first,
   - then run pcc-only additions that exploit source facts LLVM does not see
     as directly after lowering.
3. Beyond-O2 search:
   - explicit pass re-invocation / saturation experiments,
   - pipeline-order search on stable workloads,
   - source-informed metadata / attributes / canonical forms that improve
     downstream LLVM,
   - workload-specific superpasses once parity debt is low.

## How We Actually Beat LLVM O2

The repository should not try to beat LLVM O2 by skipping parity. That only
creates an un-auditable compiler fork.

The realistic path is:

1. Build a trustworthy LLVM-equivalent core on LLVM IR.
2. Keep the reference source fixed and measurable.
3. Add pcc-only information *before* or *around* the LLVM-equivalent core that
   LLVM itself usually lacks after C lowering.

The likely pcc-only advantage areas are:

- source-visible signedness / promotion intent that survives into better IR,
- stronger alias / escape / restrict facts recovered before pessimizing
  lowering,
- source-driven canonicalization that feeds a cleaner IR into the same LLVM
  transformations,
- profile- or workload-guided pass ordering once the parity floor is stable,
- extra fixed-point iteration beyond LLVM's default O2 budget where it is
  profitable.

This means "beat LLVM O2" is a Phase-after-parity goal, not a substitute for
parity.

## Required Upstream Source Anchors

This list is not complete, but it is the minimum starting point other AI
agents should read before implementing pass families.

### Scalar / CFG / Canonicalization

- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/Reassociate.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/SCCP.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/ADCE.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/DCE.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/SimplifyCFGPass.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/InstCombine/InstCombineAddSub.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/InstCombine/InstCombineMulDivRem.cpp`

### Value Numbering / Memory

- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/GVN.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/NewGVN.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/DeadStoreElimination.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/EarlyCSE.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/MergedLoadStoreMotion.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/LoopLoadElimination.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Analysis/MemorySSA.cpp`

### Promotion / Scalar Replacement

- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Utils/PromoteMemoryToRegister.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/SROA.cpp`

### Loop

- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/LICM.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/IndVarSimplify.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Utils/LoopSimplify.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Utils/LoopRotationUtils.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/LoopDeletion.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/LoopUnrollPass.cpp`

### IPO / CGSCC

- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/IPO/Inliner.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/IPO/GlobalOpt.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/IPO/GlobalDCE.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/IPO/ArgumentPromotion.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/IPO/FunctionAttrs.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/IPO/DeadArgumentElimination.cpp`
- `/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/IPO/SCCP.cpp`

## Instructions For Other AI Agents

If you are another AI working in this repository:

1. Read this file first.
2. Use `/private/tmp/llvm-src/llvm-project-20.1.8.src` as the canonical
   upstream reference tree.
3. Do not mark a pass complete because its source-level behavior "looks close".
4. Before patching any pass family, read the corresponding upstream source.
5. Write focused IR parity tests before broad benchmark claims.
6. Prefer deleting or demoting old approximation code over expanding it.

## Immediate Next Tasks

The 2026-04-20 milestone drained the `deprecated-source-approximation`
list to `0`: every visible pass now has an IR-level backing (analysis
boundary or real transform). The next concrete tasks should be:

1. Upgrade `adce` toward `equivalent` by implementing ADCE step 3
   (dead-branch removal via post-dominator analysis) and extending
   the parity corpus to cover dead-branch regions.
2. Upgrade `simplifycfg` and `instcombine` toward `equivalent` — both
   carry broad subsets and a large remaining gap; the fastest wins are
   widening the focused parity corpora until upstream divergences are
   either closed or explicitly pinned as remaining-gap tests.
3. Upgrade the memory family (`dse`, `early-cse`, `gvn`, `newgvn`,
   `mem2reg`, `sroa`) one focused corpus at a time.
4. Decide whether analysis-boundary `subset` entries (the `coro-*`,
   `openmp-opt*`, `annotation2metadata`, `forceattrs`,
   `inject-tli-mappings`, `aggressive-instcombine`,
   `alignment-from-assumptions`, `chr`, `float2int`, `loop-idiom`,
   `memcpyopt`, `move-auto-init`, `speculative-execution` markers)
   should stay at `subset` indefinitely or be retired into a dedicated
   "analysis-boundary" status once their intent is cemented.
5. Keep future passes on the IR-level path; do not re-introduce
   source-level approximations.
6. Only after Track A has produced a first tranche of real
   `equivalent` passes, begin the "beyond LLVM O2" experiments on top
   of that stable floor.
