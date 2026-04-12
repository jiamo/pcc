# All-Python LLVM Pass Translation Plan

Superseded for new optimization implementation work by:

- `docs/plans/all-pass-llvm-ir-1to1-master-plan.md`

This document remains historical context for the earlier source-level
translation route.

## Current Snapshot

- Control plane is complete: all 80 LLVM default leaf pass names are registered in the pcc pass system and can be managed through the unified pass surface.
- Semantic completion is complete: the registry is currently `80 full / 0 partial`.
- Latest default validation baseline (`2026-04-06`):
  - `env -u LC_ALL uv run pytest -q -W error::UserWarning`
  - `5442 passed in 201.16s (0:03:21)`
- Final microbenchmark matrix (`2026-04-06`, `bench/bench.py`, 80-case suite, `--opt-level 0 --opt-level 2 --runs 1`, Apple clang `15.0.0`, 1ms clean threshold):
  - `O0 all-pass` vs `O0 no-pass`: compile `1.18x`, exec `0.98x`, total `1.11x`, `78/80` matched and clean.
  - `O0 all-pass` vs `clang -O2`: compile `1.07x`, exec `2.96x`, total `1.37x`, `78/80` matched and clean.
  - `pcc -O2` vs `clang -O2`: compile `1.12x`, exec `1.00x`, total `1.08x`, `78/80` matched and clean.
- Family progress snapshot:
  - Phase 1: `22/22 full`
  - Phase 2: `15/15 full`
  - Phase 3: `17/17 full`
  - Phase 4: `12/12 full`
  - Phase 5: `14/14 full`
- Done overall is now satisfied: all 80 registry entries are `full`, the default suite is green without warnings, the benchmark matrix is recorded, and the README conclusion has been updated.
- Final interpretation: after reaching `80/80 full`, the remaining `O0 all-pass` gap versus `clang -O2` is no longer evidence of missing registry coverage. The dominant remaining gap is backend and IR-quality work beyond source-level leaf-pass translation, including SSA/backend optimization, vectorization, and cross-TU effects.
- Follow-on roadmap: the next-stage SSA MidTier and backend-parity work is tracked in `docs/plans/ssa-midtier-backend-parity-plan.md`.
- LLVM local reference checkout used for follow-on closure work:
  - `/tmp/llvm-src/`
- Follow-on rule:
  - any Phase-2/3 style work that claims parity or closure for SSA, mem2reg,
    SCCP, GVN/NewGVN, DSE/ADCE, MemorySSA-style reasoning, or loop-canonical
    forms must cite and compare against the corresponding LLVM source entry
    points first, not just benchmark behavior.
- Final closure batch beyond the earlier loop/memory work covered:
  - `aggressive-instcombine`
  - `sccp`
  - `argpromotion`
  - `called-value-propagation`
  - `callsite-splitting`
  - `globalopt`
  - `ipsccp`
  - `dse`
  - `deadargelim`
  - `elim-avail-extern`

## Goal

Finish a Python-side translation strategy for every registered LLVM leaf pass, then prove what that buys us with direct measurements.

The end state is not "all names are visible." The end state is:

- every registered pass has a defined Python-side semantics,
- every pass has focused regression coverage,
- registry entries can be promoted from `partial` to `full`,
- `allpass-no-O2` is measured directly against `clang -O2`,
- any remaining gap is explained with evidence instead of guesswork.

## Definitions

- `registered`
  - The LLVM pass name is visible and selectable through the unified pass surface.
- `partial`
  - The pass has a Python-side mapping or approximation, but the semantics are incomplete, weak, or not yet verified enough to claim equivalence.
- `full`
  - The pass has an explicit Python-side implementation or an explicit documented source-level no-op/analysis semantics, plus focused tests and benchmark evidence.
- `done overall`
  - All 80 registry entries are promoted to `full`, the default suite is green without warnings, and the final benchmark and README updates are complete.

## Completion Rules For One Pass

A pass may move from `partial` to `full` only when all of the following are true:

- The Python-side behavior is explicit.
- The intended semantics are documented in the registry notes.
- There is at least one focused regression test.
- There is at least one negative or safety regression when that makes sense for the pass.
- The default suite is green with `-W error::UserWarning`.
- The pass has been compared against the LLVM-controlled reference path on at least one representative focused case.

For backend-only or runtime-instrumentation passes that cannot be reproduced exactly at the source level, `full` still requires an explicit documented boundary. "No equivalent source-level effect" is acceptable only if it is stated clearly and tested as such.

## Validation Method

Use the unified pass system to compare the Python translation against the LLVM-visible pass name instead of treating translation as a blind rewrite.

Validation flow for a pass family:

1. Run a focused reproducer with the Python-side translation enabled.
2. Run a focused reproducer with the Python-side translation disabled.
3. Run the LLVM-controlled reference path when the pass is available through the LLVM opt pipeline.
4. Compare behavior first, then IR shape, then benchmark effect.
5. Only after that promote the registry entry.

Relevant surfaces already exist:

- `pcc --pass NAME`
- `pcc --disable-pass NAME`
- registered LLVM alias selection at `-O0`
- concrete LLVM pass selection when the matching LLVM `opt` binary is available

For the completed leaf-pass translation registry, the upstream source anchor is
already recorded in `pcc/passes/llvm_python_registry.py` via `upstream_sources`.
For the next-stage SSA/backend plan, use those registry anchors plus the local
LLVM checkout at `/tmp/llvm-src/` as the mandatory implementation reference.

This means every pass translation can be checked in three ways:

- Python translation only
- LLVM-controlled reference
- pass disabled entirely

## Work Policy

- Keep default all-pass behavior enabled in normal runs.
- Use single-pass or small-pass-family runs only for validation and measurement.
- Do not hide semantic fixes in IR text rewriting.
- Add focused regressions before broadening a patch in shared codegen or pass code.
- Keep integration runs sparse. The default suite is the incremental gate.

## Execution Phases

### Phase 1: Scalar, CFG, and Memory Simplification

Finish or deepen the source-level translations for:

- `aggressive-instcombine`
- `adce`
- `bdce`
- `correlated-propagation`
- `dse`
- `early-cse`
- `gvn`
- `instcombine`
- `instsimplify`
- `jump-threading`
- `memcpyopt`
- `mldst-motion`
- `newgvn`
- `reassociate`
- `simplifycfg`
- `sccp`
- `sroa`
- `speculative-execution`
- `constraint-elimination`
- `div-rem-pairs`
- `constmerge`
- `chr`

Phase 1 exit criteria:

- each pass above has focused tests,
- each pass above has an explicit registry note describing the Python semantics,
- the default suite is green,
- a scalar/cfg benchmark snapshot exists.

### Phase 2: IPO, Attributes, and Call-Site Transforms

Finish or deepen the translations for:

- `function-attrs`
- `rpo-function-attrs`
- `always-inline`
- `argpromotion`
- `called-value-propagation`
- `callsite-splitting`
- `deadargelim`
- `elim-avail-extern`
- `forceattrs`
- `globaldce`
- `globalopt`
- `inferattrs`
- `inline`
- `ipsccp`
- `tailcallelim`

Phase 2 exit criteria:

- pass-specific regressions exist for wrapper elimination, attribute inference, and interprocedural simplification,
- the default suite is green,
- a focused call-heavy benchmark snapshot exists,
- registry entries promoted where evidence is sufficient.

### Phase 3: Loop Pipeline

Finish or deepen the translations for:

- `indvars`
- `licm`
- `loop-idiom`
- `loop-load-elim`
- `loop-rotate`
- `loop-simplifycfg`
- `loop-deletion`
- `loop-instsimplify`
- `loop-sink`
- `loop-unroll`
- `loop-unroll-full`
- `simple-loop-unswitch`
- `extra-simple-loop-unswitch-passes`
- `loop-distribute`
- `loop-vectorize`
- `vector-combine`
- `slp-vectorizer`

Phase 3 exit criteria:

- correctness regressions exist for each loop transform that mutates structure,
- no known GCC torture or project-level loop regressions remain open,
- the default suite is green,
- a loop-heavy benchmark snapshot exists.

### Phase 4: Lowering, Builtins, Metadata, and Runtime Helpers

Finish or deepen the translations for:

- `infer-alignment`
- `annotation2metadata`
- `lower-expect`
- `mem2reg`
- `float2int`
- `lower-constant-intrinsics`
- `alignment-from-assumptions`
- `inject-tli-mappings`
- `libcalls-shrinkwrap`
- `move-auto-init`
- `rel-lookup-table-converter`
- `verify`

Phase 4 exit criteria:

- builtin and libc rewrites have positive and negative regressions,
- metadata-related passes have explicit scope limits,
- the default suite is green,
- focused libc-heavy checks are stable.

### Phase 5: Coroutine, OpenMP, Instrumentation, and Analysis-Only Boundaries

Finish or explicitly bound the translations for:

- `coro-early`
- `coro-elide`
- `coro-split`
- `coro-annotation-elide`
- `coro-cleanup`
- `openmp-opt`
- `openmp-opt-cgscc`
- `ee-instrument`
- `require`
- `invalidate`
- `recompute-globalsaa`
- `cg-profile`
- `annotation-remarks`
- `transform-warning`

Phase 5 exit criteria:

- every pass above has either a real source-side translation or a documented no-op/analysis-only boundary,
- the registry notes make that boundary explicit,
- the default suite is green,
- phase-boundary integration checks are run once.

### Phase 6: Final Reclassification and Proof

After Phases 1 through 5:

- promote every remaining `partial` entry to `full` only if it satisfies the completion rules,
- leave any truly irreducible source-level gap documented explicitly instead of hand-waving it away,
- run the final benchmark matrix,
- update the README with the final conclusion.

## Benchmark Matrix

At the end of each major phase, run:

- `allpass-no-O2` vs `no-pass-no-O2`
- `allpass-no-O2` vs `clang -O2`
- `pcc -O2` vs `clang -O2`

For passes that materially affect runtime, also run targeted ablations:

- pass family enabled vs disabled,
- Python translation vs LLVM-controlled reference,
- whole-pipeline effect after the phase lands.

If `allpass-no-O2` still trails `clang -O2` after all 80 passes are `full`, that becomes evidence that the remaining gap is not "missing registry coverage." At that point the gap must be classified explicitly, for example:

- SSA-level effects not reproducible source-side,
- vectorization/backend-only effects,
- codegen quality differences after equivalent source transforms,
- missing cross-TU optimization.

## Backlog Ownership By Family

This is the authoritative translation backlog for the current registry.

### Scalar / CFG / Memory

- `aggressive-instcombine`
- `adce`
- `bdce`
- `correlated-propagation`
- `dse`
- `early-cse`
- `gvn`
- `instcombine`
- `instsimplify`
- `jump-threading`
- `memcpyopt`
- `mldst-motion`
- `newgvn`
- `reassociate`
- `simplifycfg`
- `sccp`
- `sroa`
- `speculative-execution`
- `constraint-elimination`
- `div-rem-pairs`
- `constmerge`
- `chr`

### IPO / Attributes / Calls

- `function-attrs`
- `rpo-function-attrs`
- `always-inline`
- `argpromotion`
- `called-value-propagation`
- `callsite-splitting`
- `deadargelim`
- `elim-avail-extern`
- `forceattrs`
- `globaldce`
- `globalopt`
- `inferattrs`
- `inline`
- `ipsccp`
- `tailcallelim`

### Loop Pipeline

- `indvars`
- `licm`
- `loop-idiom`
- `loop-load-elim`
- `loop-rotate`
- `loop-simplifycfg`
- `loop-deletion`
- `loop-instsimplify`
- `loop-sink`
- `loop-unroll`
- `loop-unroll-full`
- `simple-loop-unswitch`
- `extra-simple-loop-unswitch-passes`
- `loop-distribute`
- `loop-vectorize`
- `vector-combine`
- `slp-vectorizer`

### Lowering / Builtins / Metadata

- `infer-alignment`
- `annotation2metadata`
- `lower-expect`
- `mem2reg`
- `float2int`
- `lower-constant-intrinsics`
- `alignment-from-assumptions`
- `inject-tli-mappings`
- `libcalls-shrinkwrap`
- `move-auto-init`
- `rel-lookup-table-converter`
- `verify`

### Coroutine / OpenMP / Instrumentation / Analysis

- `coro-early`
- `coro-elide`
- `coro-split`
- `coro-annotation-elide`
- `coro-cleanup`
- `openmp-opt`
- `openmp-opt-cgscc`
- `ee-instrument`
- `require`
- `invalidate`
- `recompute-globalsaa`
- `cg-profile`
- `annotation-remarks`
- `transform-warning`

## Tracking Rules

This file should be updated whenever one of these happens:

- a pass moves from `partial` to `full`,
- a new focused regression is added for a tracked pass,
- a benchmark phase completes,
- a source-level pass is declared intentionally analysis-only,
- the final explanation for `allpass-no-O2` vs `clang -O2` changes.

This file is now the completion record for the all-Python pass translation effort and should stay in sync if the registry or the benchmark conclusion changes again.
