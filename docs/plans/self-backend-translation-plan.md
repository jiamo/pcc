# Self Backend Translation Plan

Related plans:

- `docs/plans/dual-llvm-backend-compat-plan.md`
- `docs/plans/llvmcapi-wire-spike-report.md`
- `docs/plans/all-pass-llvm-ir-1to1-master-plan.md`
- `docs/plans/python-frontend-plan.md`

## Canonical LLVM Reference Trees

Verified on this machine on `2026-04-20`.

Use these exact absolute roots when reading upstream LLVM source for the self
backend track:

- primary monorepo root:
  - `/private/tmp/llvm-src/llvm-project-20.1.8.src`
- split-tree LLVM source mirror:
  - `/private/tmp/llvm-src/llvm-20.1.8.src`
- local clang/frontend reference tree:
  - `/private/tmp/llvm-clang-tests/clang`

Rules:

- treat the monorepo root as the primary documentation anchor,
- when the local monorepo checkout is sparse for a needed backend subtree,
  expand/sync the checkout before implementation instead of guessing from
  memory,
- every real self-backend subsystem change must cite at least one upstream LLVM
  source entry point in the task notes / code comments / plan updates,
- if `pcc` intentionally implements a narrower subset than LLVM, document that
  boundary explicitly instead of presenting it as full parity.

## Status

Proposed active roadmap for the **own machine backend** track.

Snapshot as of `2026-04-20`:

- Phase 0 groundwork has started landing in repo code:
  - backend selector skeleton (`--backend`, `PCC_BACKEND`),
  - backend-aware compile/native/JIT cache identities,
  - focused selector/cache/API regression coverage,
- the first Phase-2 code start now exists behind explicit emit mode:
  - `--backend=self --emit-asm` can lower a tiny truthful AArch64 Darwin MVP
    subset (current slice already covers simple `i32` arguments, direct calls,
    basic integer arithmetic, `alloca`/`load`/`store` for local `i32` slots,
    compares, conditional branches, phi joins, and loop-shaped CFGs within that
    bounded integer subset),
- the asm-first path now also has first execution/artifact closure:
  - `--backend=self` can execute supported programs by assembling/linking via
    the host toolchain,
  - `--backend=self --emit-obj` can assemble the supported subset into a Mach-O
    object file through the host assembler driver,
- scalar coverage has started broadening beyond the first `i32` slice:
  - `i64` call/return and common scalar casts are now started,
  - pointer args/returns plus direct pointer load/store through supported
    scalar pointer paths are now started,
  - simple scalar global data objects are now started,
- the non-integer scalar frontier is now also started for the first truthful
  slice:
  - `float` / `double` args, returns, loads/stores, arithmetic, compares, and
    common int↔fp casts are now started,
  - immediate fp constants are now started for the common hex-encoded LLVM IR
    constant form,
- address-calculation and aggregate-adjacent lowering have also started:
  - first `getelementptr` coverage now exists for local arrays, simple named
    structs, and string/array decay patterns in the supported scalar subset,
  - local string literals, simple global string-pointer initializers, and basic
    scalar global-array constants are now started,
- aggregate ABI has also started at the first narrow truthful boundary:
  - small aggregate-by-value passing/return (for register-sized integer/pointer
    simple struct shapes) is now started,
  - that now includes both single-register cases and the first two-register
    partial-tail slice (`8+{1,2,4,8}` byte chunk shapes),
- broader aggregate ABI, richer rodata/global-init forms, and fuller ABI
  closure are still open work,
- `β4.0` seam mapping and the real LLVM-source-backed lowering plan are still
  the next prerequisite for serious backend work.

This plan is intentionally separate from the llvmlite-removal / `llvm_capi`
wire-up work. They are adjacent, but not the same problem:

- **β4 / llvm_capi text-first builder** answers: how do we stop depending on
  `llvmlite` while preserving the current LLVM-based pipeline?
- **self backend translation** answers: how do we gradually stop relying on
  LLVM's machine backend for native emission?

The first is a shared-core extraction step. The second is a new backend epic.

---

## Why This Exists

`pcc` now owns a large amount of LLVM-facing optimization logic in Python:

- the visible pass surface is no longer "LLVM does everything for us",
- the repository already has substantial LLVM-IR-level reasoning and rewrite
  machinery,
- the next long-horizon question is whether native emission can also become
  `pcc`-owned.

That does **not** mean the machine backend is "almost done". It means the
project now has enough control over the front half and middle-end to make a
self backend realistic as a staged effort.

This plan exists so that backend work can begin **without destabilizing the
current compiler**.

---

## Non-Negotiable Goal

Build a `pcc`-owned native backend that can be selected explicitly while the
current LLVM backend remains the default until the new path is proven.

The end state is:

- `pcc` can run with `--backend=llvm` and `--backend=self`,
- the self backend starts with a narrow but truthful subset,
- unsupported cases fall back or fail loudly instead of silently miscompiling,
- backend work does not fork the frontend or optimizer stacks,
- the long-term self-host path can choose between LLVM-backed emission and
  `pcc`-owned emission behind one backend contract.

---

## Hard Policy

### 1. Default behavior must not regress

Until explicitly promoted, the default backend remains the current LLVM path.
All broad regression gates continue to run against that default path.

### 2. No silent wrong-code boundary

The self backend must prefer:

- explicit fallback to LLVM,
- or explicit "unsupported" failure,

rather than guessing through unsupported lowering.

### 3. Backend work must reuse shared front-half infrastructure

Do not create a second isolated frontend/codegen stack just to feed the self
backend. The long-term value comes from reusing:

- parser and semantic lowering,
- internal SSA / pass information,
- artifact pipeline,
- cache discipline,
- CLI/backend selection.

### 4. Start asm-first, not object-writer-first

The first useful self backend should emit assembly for one target and rely on
system assembler/linker tooling. Object writer, relocation encoding, and wider
MC-format ownership come later.

### 5. Single target first

Do not start with "portable backend" ambitions. First closure means one real
host target works end-to-end.

Recommended first target for this repository's current environment:

- **AArch64 Darwin**

A later Linux `x86_64` track can follow once the backend contract is stable.

---

## Architecture Direction

The target long-form architecture is:

```text
frontend / semantic lowering / SSA / passes
  │
  ├─ shared builder / shared artifact pipeline
  │
  └─ backend contract
       ├─ llvm backend       (default, existing path)
       ├─ llvm_capi backend  (llvmlite replacement path)
       └─ self backend       (new machine backend path)
```

Important consequence:

- `β4` text-first builder work is **not wasted** if the project later adopts a
  self backend.
- That work is the cleanest way to detach the front half from `llvmlite` and
  make backend selection real.

The self backend should be able to consume either:

1. an LLVM-IR-like textual/structured form as a bootstrap input, or
2. a later `pcc`-owned lower machine/MIR form.

The initial implementation may choose (1) for speed, but the architecture must
not assume the self backend will always parse LLVM IR text forever.

---

## Scope Boundaries

### In scope for the first wave

- backend selector plumbing,
- backend-aware cache and artifact separation,
- single-target assembly emission,
- integer/pointer scalar lowering,
- basic calls, returns, branches, stack slots, and local frame layout,
- a minimal register-allocation strategy,
- focused parity/regression harnesses,
- explicit fallback boundary to LLVM for unsupported cases.

### Out of scope for the first wave

- multi-target support,
- debug info parity,
- custom object writer,
- exceptions/unwind,
- vector ISA exploitation,
- complete ABI corner closure for every C construct,
- replacing LLVM for all project configurations.

---

## Translation Strategy

The project should **not** literally port LLVM backend C++ line-by-line.

Preferred strategy:

- translate **semantic contracts**,
- translate **algorithmic intent**,
- preserve upstream-inspired safety boundaries where useful,
- but implement a smaller, more Pythonic backend organization.

In other words:

- use LLVM source as oracle and algorithm reference,
- do **not** import LLVM's historical C++ structure wholesale unless it is the
  simplest truthful option.

## LLVM Source Anchor Map For The First Real Self-Backend Slice

This plan is not allowed to drift into "invent a plausible backend" work.

For the first target (`AArch64 Darwin`, asm-first), use these upstream source
entry points as the primary oracle set:

### Target-independent codegen / lowering references

- target-lowering contracts and legalization:
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/CodeGen/TargetLoweringBase.cpp`
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/CodeGen/SelectionDAG/SelectionDAGISel.cpp`
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/CodeGen/SelectionDAG/LegalizeDAG.cpp`
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/CodeGen/SelectionDAG/LegalizeTypes.cpp`
- calling-convention helpers / lowering mechanics:
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/CodeGen/SelectionDAG/CallingConvLower.cpp`
- register allocation and spill/reference algorithms:
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/CodeGen/RegAllocFast.cpp`
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/CodeGen/PrologEpilogInserter.cpp`
- asm emission contracts:
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/CodeGen/AsmPrinter/AsmPrinter.cpp`

### AArch64-specific oracle set

- target lowering / instruction selection intent:
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/Target/AArch64/AArch64ISelLowering.cpp`
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/Target/AArch64/AArch64InstrInfo.cpp`
- frame / callee-save / stack layout intent:
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/Target/AArch64/AArch64FrameLowering.cpp`
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/Target/AArch64/AArch64RegisterInfo.cpp`
- calling convention and ABI details:
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/Target/AArch64/AArch64CallingConvention.td`
- target-specific assembly emission / MC syntax:
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/Target/AArch64/AArch64AsmPrinter.cpp`
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/Target/AArch64/MCTargetDesc/AArch64MCAsmInfo.cpp`
  - `/private/tmp/llvm-src/llvm-20.1.8.src/lib/Target/AArch64/MCTargetDesc/AArch64MCTargetDesc.cpp`

Required discipline for this oracle map:

- do not translate LLVM line-by-line,
- do not pick algorithms from memory without checking these sources first,
- when `pcc` deliberately chooses a smaller algorithm (for example linear-scan-
  style allocation instead of LLVM's fuller allocator stack), record that as an
  intentional subset rather than as implicit equivalence.

---

## Phases

### Phase 0: Backend Selection And Safety Rails

Goal: make backend selection real before the self backend exists.

Current status (`2026-04-20`): the first selector/cache/test slice is landed;
capability-model broadening and explicit fallback result typing remain open.

Deliverables:

- `--backend=` CLI and `PCC_BACKEND` env plumbing,
- backend capability model (`jit`, `emit_obj`, `emit_asm`, `debug`, `cross`),
- backend-aware compile cache keys,
- explicit unsupported/fallback result types,
- small selector tests proving default behavior is unchanged.

Exit criteria:

- default backend behavior is identical to today,
- backend mode changes cannot reuse stale cache artifacts,
- non-default backend selection is visible and auditable.

### Phase 1: Builder / Front-Half Decoupling

Goal: stop tying the frontend directly to `llvmlite` so multiple backend
providers can consume the same front-half output.

Primary dependency:

- `β4` text-first builder / `llvm_capi` work from
  `docs/plans/llvmcapi-wire-spike-report.md`

Deliverables:

- shared builder surface not owned by `llvmlite`,
- LLVM-backed backends still passing the existing gates,
- a stable handoff point where the self backend can begin consuming compiler
  output.

Exit criteria:

- front-half codegen is no longer architecturally trapped inside
  `llvmlite.ir`,
- the self backend has a real integration seam.

### Phase 2: Single-Target Asm-First MVP

Goal: produce runnable assembly for one host target.

Recommended subset:

- integer and pointer arithmetic,
- compares and conditional branches,
- direct calls and returns,
- stack locals and simple loads/stores,
- global-address references,
- simple aggregate-adjacent cases only when already represented as scalarized
  operations by the front half.

Deliverables:

- target register set model,
- calling convention subset,
- frame layout and stack alignment,
- instruction selection for the MVP opcode set,
- a simple register allocator (linear scan is acceptable),
- assembly printer,
- execution via system assembler/linker.

Exit criteria:

- a focused backend corpus runs correctly under `--backend=self`,
- unsupported shapes fail loudly or fall back,
- default LLVM backend remains unaffected.

### Phase 3: Self-Hosted Compiler Slice

Goal: make the self backend useful for a controlled slice of compiler and test
workloads.

Deliverables:

- compile a meaningful subset of repository cases with `--backend=self`,
- document precise supported C/IR subset,
- run selected self-host or compiler-internal workloads on the self backend,
- add backend differential tests against the LLVM path.

Exit criteria:

- self backend is no longer just a toy case runner,
- it can execute a curated real slice without manual patching.

### Phase 4: ABI And Coverage Expansion

Goal: reduce the unsupported subset without losing truthfulness.

Potential expansion areas:

- more aggregate passing/return cases,
- more indirect call patterns,
- better spill/reload quality,
- more robust branch lowering,
- improved lowering of addressing modes,
- broader local-memory patterns.

Exit criteria:

- the supported corpus grows materially,
- backend crashes/unsupported exits shrink with evidence,
- no increase in silent wrong-code risk.

### Phase 5: Decide Whether Full Machine Ownership Is Worth It

Goal: make an evidence-based decision on whether to continue beyond asm-first.

Questions to answer:

- Is the self backend already good enough for the target product shape?
- Is object-writer ownership actually worth the added complexity?
- Does the project need more than one target?
- Is LLVM still the better default even after the self backend matures?

This phase may end in any of three honest outcomes:

1. keep LLVM as default and retain self backend as a niche/experimental path,
2. promote self backend for a narrow product subset,
3. continue toward broader machine-backend ownership.

---

## Token Budget

These are planning-level estimates, not commitments.

### Phase 0 + Phase 1 (selection + decoupling prerequisites)

- `repo tokens`: `40k-120k`
- `working tokens`: `500k-1.8M`

### Phase 2 (single-target asm-first MVP)

- `repo tokens`: `180k-420k`
- `working tokens`: `3M-9M`

### Phase 3 + Phase 4 (usable backend slice + coverage growth)

- `repo tokens`: `180k-500k`
- `working tokens`: `3M-10M`

### Total through a serious first-generation self backend

- `repo tokens`: `400k-1.0M`
- `working tokens`: `6.5M-20M`

Interpretation:

- this is **not** a tiny follow-up patch after pass translation,
- but it is still within the same rough order of magnitude as a major roadmap
  epic, not an impossible rewrite of all LLVM.

---

## Validation Strategy

### 1. Backend differential testing

For every backend milestone, compare:

- `--backend=llvm`
- `--backend=self`

on:

- focused minimized C probes,
- existing sensitive runtime tests,
- curated integration slices.

### 2. Layered parity, not premature byte-identical fetish

Validation order should be:

1. semantic correctness,
2. structural/backend contract correctness,
3. textual/stylistic convergence where useful.

The self backend does **not** need to produce byte-identical LLVM-path output to
count as correct.

### 3. Explicit unsupported accounting

Track and report:

- unsupported feature exits,
- fallback frequency,
- backend-only crashes,
- wrong-code regressions separately from unsupported cases.

---

## Immediate Task Queue

### T0. Approve backend contract split

Decision to record explicitly:

- `llvm` remains default,
- `llvm_capi` is the llvmlite-removal track,
- `self` is the machine-backend track,
- `β4` is shared groundwork for both LLVM-backed and self-backed futures.

### T1. Implement selector skeleton — done

Files likely involved:

- `pcc/pcc.py`
- `pcc/evaluater/c_evaluator.py`
- new `pcc/backend/`

Landed slice:

- explicit `--backend` / `PCC_BACKEND`,
- default path still `llvm`,
- explicit unsupported failure for `self`.

### T2. Make cache signatures backend-aware — done

Files likely involved:

- `pcc/evaluater/c_evaluator.py`
- cache tests in `tests/`

Landed slice:

- compile cache signature includes backend identity,
- native cache signature includes backend identity,
- in-memory JIT cache identity includes backend identity.

### T3. Add backend selector tests — done

Examples:

- default stays `llvm`,
- `--backend=llvm` is a no-op behaviorally,
- `--backend=self` fails clearly while unimplemented,
- backend selection changes cache identity.

### T4. Land β4.0 surface trace — next active prerequisite

Before serious self-backend work, record the real builder/backend surface used by
`pcc` so the front-half seam is data-driven instead of guessed.

Deliverable for this task:

- a concrete map from current `pcc` call sites to LLVM / llvmlite surfaces,
- a list of which surfaces are shared prerequisites for both `llvm_capi` and
  `self`,
- an explicit "do not start self lowering before this is written down" gate.

### T5. Choose first target and freeze MVP subset — ready after T4

Recommended default choice for this repository today:

- target: `aarch64-apple-darwin`
- output: assembly first
- linker path: system toolchain

First closure target should be phrased concretely as:

- emit Mach-O-compatible AArch64 assembly accepted by the host `cc`/`clang`
  driver,
- no object-writer ownership in the MVP,
- no silent fallback inside the self backend once a function has entered the
  claimed-supported subset.

---

## Definition Of Done For The First Real Milestone

The first meaningful closure point is **not** "replace LLVM".

It is:

- backend selection exists,
- default path is unaffected,
- self backend can be chosen explicitly,
- self backend emits runnable assembly for a narrow truthful subset on one
  target,
- unsupported cases are explicit,
- at least one curated real workload slice passes under `--backend=self`.

That milestone is large enough to matter and small enough to ship honestly.
