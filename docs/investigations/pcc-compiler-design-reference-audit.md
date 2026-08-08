# Investigation: compiler-design references for faster, exact, maintainable PCC

## Status

resolved

## Problem Description

PCC must become materially faster without reducing Python semantics, removing
tests, hiding work behind larger timeouts, or making NumPy, a GC backend, a
benchmark, or the current bootstrap graph into a compiler special case.  The
immediate symptom is repeated work in the self-host integration matrix; the
larger question is which techniques from the Cornell Virtual Workshop, CPython,
Go, HotSpot, GraalVM, and LLVM fit PCC's AOT, no-libpython, self-owned design.

This document classifies those techniques against four non-negotiable PCC
boundaries:

1. ordinary Python retains dynamic dispatch, identity, exceptions, arbitrary
   precision integers, and observable mutation;
2. optimized paths need a guard or proof and an exact generic slow path;
3. the self backend remains an owner, while LLVM and external runtimes remain
   labeled oracles or optional providers;
4. compile-time and runtime improvements need phase, RSS, throughput, and
   semantic evidence rather than intuition.

Predecessor measurements and fixes are in
[`self-bootstrap-146-module-ir-emission-regression.md`](self-bootstrap-146-module-ir-emission-regression.md),
[`self-backend-short-lived-emit-worker-fanout.md`](self-backend-short-lived-emit-worker-fanout.md),
and
[`pcc1-package-graph-frontend-worker-memory-budget.md`](pcc1-package-graph-frontend-worker-memory-budget.md).

### Update 2026-07-22: GPU references must produce an owned implementation

The reference audit is not complete if it only lists techniques.  The expanded
scope includes TVM/TIRx, TileLang, MLIR GPU/Transform, and Apple Metal, and the
highest-confidence GPU finding must land as a finite PCC-owned implementation.
The first accepted slice is an owner-neutral, replayable schedule module for
checked Metal thread binding.  It is intentionally narrower than a claim of
general tiling, layout scheduling, software pipelining, or autotuning.

## Repro

The exact non-integration suite completed with `9456 passed, 114 skipped in
781.31s`.  The exact integration command below reached its 1800-second watchdog
without a pytest summary:

```bash
gtimeout 1800s env -u LC_ALL uv run pytest -m integration
```

At the watchdog, ordinary integration cases had completed and the GC2/GC3
self-host chains were still compiling.  The concurrent-run profile
`build/bootstrap-pytest-self-gc2/profile/stage2.json` records 203,903,125 input
IR bytes, 152 modules, 208.937 seconds in parallel frontend codegen, and
262.113 seconds total.  All 271 final object-cache lookups were hits.  This is a
locator, not the isolated performance baseline: concurrent chains can contend,
so Proposal No.1 requires an isolated cold/warm A/B before claiming a speedup.

Expected result: the exact integration suite finishes inside the tightened
900-second goal budget with a final pytest summary.  No five-GC stage or
fixed-point assertion may be removed.

## Test [CONFIRMED]

The 1800-second exact integration timeout and surviving work boundary above
were observed on 2026-07-22.  All timed-out child process groups were then
terminated and checked; no `pytest`, `bootstrap.sh`, `pcc`, `pcc1`, `pcc2`, or
`pcc3` child remained.

The isolated current-source pcc1-to-pcc2 A/B is complete.  The cold content
cache run took 373.914 seconds at 5.319 GiB RSS (153 modules and 204,570,740 IR
bytes); an exact repeat took 38.209 seconds at 2.689 GiB RSS.  Frontend codegen
fell from 83.233 seconds to a 0.393-second cache lookup, and all 272 self-backend
objects hit.  A separately keyed pcc2-to-pcc3 run still performed a real compile
and passed normalized fixed-point comparison, so the pcc1 cache cannot mask a
changed compiler.  The 900-second forced five-GC and exact-suite gates remain
pending.

## Reference Findings

### Cornell: optimize the work and memory path before tuning instructions

The [Cornell Code Optimization roadmap](https://cvw.cac.cornell.edu/code-optimization)
describes a useful order of attack: improve the model and algorithm first,
then high-level structure and locality, then compilation, and only then tune
measured execution hot spots.  Its advice maps to PCC as follows:

| Cornell principle | PCC interpretation | Verdict |
|---|---|---|
| Profile, change the dominant block, and repeat | Keep machine-readable phase counters; optimize isolated `multi_frontend_codegen_parallel` before smaller link helpers | adopt now |
| Prefer unit-stride access and reuse fetched data | Replace pointer-heavy compiler maps/objects with compact IDs, arenas, and contiguous tables where profiles show traversal cost | adopt after measurement |
| Hoist invariant work only when aliasing permits it | Add an explicit effect/alias contract; Python calls, descriptors, globals, and extension calls are effectful unless proven otherwise | adopt with proof/guards |
| Vectorize independent loops | Restrict widening to typed value/buffer/kernel loops with alias, stride, alignment, trip-count, overflow, and exception guards | adopt with scalar slow path |
| Use blocking/tiling for cache reuse | Apply to Kernel IR, Metal/TIRx tensor loops, and dense compiler analyses; do not tile arbitrary Python iteration | adopt in the relevant IR |
| Use optimized libraries | Dispatch through generic buffer/capsule/ABI contracts to BLAS, Accelerate, or device libraries; never branch on a package name | adopt |
| Use architecture-specific flags | Record an explicit target/cpu/features key and provide portable defaults; never make `-march=native` an unlabeled default | explicit opt-in only |
| Inspect output and vectorization reports | Emit optimization remarks with proof, rejection reason, and slow-path identity | adopt |

Cornell's pages on [compiler options](https://cvw.cac.cornell.edu/code-optimization/opt-via-compilers/compiler-options),
[data locality and latency hiding](https://cvw.cac.cornell.edu/code-optimization/cache-considerations/adv-cache-topics),
[multicore cache sharing](https://cvw.cac.cornell.edu/code-optimization/cache-considerations/multicore-cache-sharing),
and [array blocking](https://cvw.cac.cornell.edu/code-optimization/cache-considerations/array-blocking)
also explain why unbounded parallelism is not a speed design: cache contention,
false sharing, retained worker heaps, and memory bandwidth can make additional
workers slower.  PCC therefore needs a resource-budgeted action scheduler, not
`cpu_count()` fanout.

### CPython: cheap specialization, compact frames, and generated contracts

[PEP 659](https://peps.python.org/pep-0659/) specializes very small operations,
keeps compact inline state, checks assumptions cheaply, and deoptimizes on a
miss.  Current CPython also uses lightweight frames allocated contiguously on a
per-thread stack and materializes a full frame object only when observation
requires one; its interpreter definitions generate executable cases and
metadata from one DSL.  The experimental tier-2 pipeline translates hot
specialized bytecode to micro-ops and then uses optimized IR plus copy-and-patch
machine-code templates, as summarized in the
[CPython 3.13 architecture notes](https://github.com/python/cpython/blob/main/Doc/whatsnew/3.13.rst).

PCC should borrow the contract, not the interpreter shape:

- guarded direct call, global, attribute, container, and numeric paths can
  coexist with exact generic runtime operations;
- cache keys need type/class/dict-layout or function-version guards and a
  deoptimization/miss counter;
- frames and compiler values may use compact internal projections while
  observation (`sys._getframe`, traceback, weakref, identity escape) boxes or
  materializes the semantic object;
- runtime ABI operations and their trace/update metadata should be generated
  from one specification instead of maintained in parallel C and pcc-Python
  tables.

PCC should not replace its AOT/self-host root with CPython bytecode quickening.
An optional long-running tier may consume counters later, but pcc1/pcc2/pcc3
and native artifacts must remain independently valid.

### Go: action graphs, compact SSA, explicit memory dependencies, and evidence

Go's build system stamps actions with a hash of their inputs and artifacts with
a hash of their outputs, allowing installed or cached package artifacts to be
reused.  The design is visible in Go's
[`buildid.go`](https://go.dev/src/cmd/go/internal/work/buildid.go).  This is the
closest reference for PCC's immediate problem: represent source discovery,
parse/type/export, module IR, IR transforms, object emission, runtime archive,
and link as separate content-addressed actions rather than one repeated
whole-program procedure.

The [Go compiler overview](https://go.dev/src/cmd/compile/README) separates
syntax, types, serialized Unified IR/export data, middle-end, lowering, SSA,
and object generation.  Its
[SSA design](https://go.dev/src/cmd/compile/internal/ssa/README) uses dense
sequential IDs to avoid maps, makes memory state an SSA value so stores cannot
be reordered incorrectly, runs function-scoped passes, and generates simple
rewrite code from declarative rules.  It also exposes per-phase timing,
optimization diagnostics, assembly, and before/after SSA views.  PCC should
adopt those observability and representation properties, while preserving its
Python-specific effects, ownership, exception, and GC-root edges.

Go's [PGO design](https://go.dev/doc/pgo) is deliberately conservative,
content-addressed, and robust to source drift.  A PCC profile must likewise be
bound to semantic mode, target, and code identity; unmatched samples must
degrade to unprofiled compilation, never to guessed semantics.

### HotSpot/JVM: tiering, scalar replacement, and lifecycle-separated caches

HotSpot combines fast startup/profile collection with later optimization
through [tiered compilation](https://docs.oracle.com/en/java/javase/17/vm/java-hotspot-virtual-machine-performance-enhancements.html).
It also separates short-lived profiled code, long-lived optimized code, and
non-method code into different cache segments, reducing scan time and
fragmentation.  Its escape analysis classifies allocations and removes
non-escaping, scalar-replaceable objects and redundant locks.

For PCC this implies:

- keep quick baseline/AOT artifacts distinct from optional profile-specialized
  artifacts and key both explicitly;
- separate temporary compiler products, reusable IR/objects, and final native
  artifacts, each with a size/age policy;
- add escape analysis only after identity, exception, finalizer, weakref,
  coroutine-frame, C-extension, and GC-root escapes are modeled.  A value may
  be scalar-replaced only when materialization at every observation point is
  proven.

HotSpot's dynamic JIT is not a reason to defer AOT correctness, and Java's
fixed-width primitives are not a valid model for Python `int`.  PCC's tagged
small-int lane must promote to arbitrary precision on overflow.

### GraalVM: partial evaluation and a shared optimization graph

The [Graal compiler](https://www.graalvm.org/jdk21/reference-manual/java/compiler/)
uses a language-independent graph; Truffle partially evaluates interpreter AST
and framework code into that graph, optimizes it, installs machine code, and
redirects hot execution.  It is especially effective when escape analysis can
remove abstraction-related allocations.

PCC can use the same principle at two explicit boundaries:

1. partially evaluate pcc-Python runtime helpers when arguments, type layout,
   and effects are compile-time stable, leaving a guard and generic runtime
   call when they are not;
2. converge CPU value operations and Kernel IR on a small owner-neutral graph
   vocabulary without importing or executing TVM/TileLang as an owner.

PCC must not assume that arbitrary Python ASTs are stable Truffle nodes.  Class
mutation, monkey-patching, descriptors, globals, tracing, and extension calls
must either invalidate the specialization or remain on the generic path.

### LLVM: analysis invalidation, scalable summaries, and legality/cost models

LLVM's [new pass manager](https://releases.llvm.org/15.0.0/docs/NewPassManager.html)
caches analysis results and requires transformations to declare what remains
valid.  It prevents inner passes from triggering arbitrary outer analyses,
which avoids quadratic rescans and prepares deterministic parallelism.  PCC's
passes need the same `requires / preserves / invalidates` contract before
adding more transformations.

[ThinLTO](https://clang.llvm.org/docs/ThinLTO.html) keeps compact per-module
summaries, performs a cheap combined-index analysis, runs transformations in
parallel module backends, and offers a prunable incremental cache.  PCC should
use this architecture for cross-module types, effects, exports, call edges, and
specialization candidates: share summaries first; import or recompile a body
only when the action key says it changed.

LLVM's [vectorizers](https://llvm.org/docs/Vectorizers.html) separate legality
from profitability, emit missed-optimization reasons, add runtime no-alias
checks when static proof is absent, retain scalar remainder/fallback loops, and
avoid floating-point reassociation unless the semantic mode permits it.  PCC
needs those exact boundaries in both LLVM-backed and self-backed paths; letting
LLVM silently vectorize while the self backend cannot express the same guarded
plan would fail the owner requirement.

### TVM, TileLang, MLIR, and Metal: separate semantics, schedules, and targets

The reference source used for this section is pinned outside the PCC tree:

| Reference | Local path | Source identity | Relevant implementation |
|---|---|---|---|
| Apache TVM | `~/pcc_refs/apache-tvm-full-depth1` | `cfb98e938c8d9525648c75fbebcb8944edb952fe` | `python/tvm/s_tir/schedule/`, `python/tvm/s_tir/dlight/`, `src/s_tir/meta_schedule/` |
| TileLang | `~/pcc_refs/tilelang-full-depth1` | `dff136d4da552389b0a41f394edfa1a9fe47a590` | `tilelang/engine/lower.py`, `tilelang/backend/pass_pipeline/`, `src/transform/`, `src/metal/` |

TVM's current architecture deliberately separates core `tirx` payload IR and
lowering from `s_tir` schedule state, trace, DLight rules, and MetaSchedule.
The [TVM architecture guide](https://tvm.apache.org/docs/arch/index.html) names
tiling, vectorization, and thread binding as schedule operations over
`tirx::PrimFunc`.  Its `Trace` can be serialized and replayed, while schedule
analysis checks legality before mutation.  DLight supplies zero-search rules;
[MetaSchedule](https://tvm.apache.org/docs/deep_dive/tensor_ir/tutorials/meta_schedule.html)
adds candidate generation, builders/runners, a cost model, measurements, and a
database.  PCC should copy that separation, not TVM's object model or runtime.

TileLang's current `engine/lower.py` performs a semantic pre-lower check, then
resolves one target pass-pipeline adapter and splits host/device IR before
device code generation.  The Metal implementation has explicit target codegen
and transforms rather than running CUDA passes and hoping they degrade.  Its
schedule rules are useful references, but upstream support is not PCC semantic
ownership.  PCC's pinned optional provider therefore remains an execution
owner only after it consumes PCC-validated frozen IR, records its exact pass
identity and dependency hashes, and returns through PCC's packed-argument and
fence interfaces.

MLIR's [Transform dialect](https://mlir.llvm.org/docs/Dialects/Transform/)
provides the most relevant correctness rule: transform IR is distinct from
payload IR, handles have checked types and invalidation, and a sequence fails
when a precondition is not met.  Its
[GPU dialect](https://mlir.llvm.org/docs/Dialects/GPU/) separately models
kernel IR, target attachment/serialization, and offload translation.  PCC does
not need to import MLIR to adopt those interfaces: a schedule record can bind
to an exact Kernel IR digest, target, selector, and expected old state, then be
applied once before the plain-TIR freeze.

Apple's `MTLComputePipelineState` exposes `threadExecutionWidth`,
`maxTotalThreadsPerThreadgroup`, and static threadgroup memory.  These are the
right measured inputs for a later cost/capability adapter; they are not license
to choose a schedule from ambient hardware without recording it.  Apple also
documents [Metal binary archives](https://developer.apple.com/documentation/metal/metal-binary-archives)
as GPU-specific precompiled pipeline slices that avoid repeated runtime shader
compilation.  PCC should key any future archive by scheduled IR, Metal source,
pipeline descriptor, toolchain/OS, and device family, and keep Metal IR as the
exact fallback.  That is a later cache slice, not part of the first schedule
instruction.

The implementable order is therefore:

| Slice | PCC interface and safety rule | Status |
|---|---|---|
| Replayable thread binding | schedule bound to semantic IR digest, target, function, and expected old binding; apply before freeze in both owners | implemented and focused gates green |
| Typed tile/layout/pipeline transforms | distinct instruction types with pre/postconditions; never an arbitrary attrs dictionary | next design slice |
| Rule-based Metal schedule | deterministic DLight-like adapter from explicit device capabilities; preserve scalar schedule | profile-gated |
| Measured autotuning | isolated builder/runner, CPU oracle, bounded trials, content-addressed measurement DB, deterministic selected record | later; not a default compile dependency |
| Metal pipeline/archive cache | include scheduled IR, source, descriptor, toolchain/OS/device identity; validate artifact hashes | later compile/runtime efficiency slice |
| Cross-owner schedule parity | pcc-metal and tvm-tilelang consume the same scheduled frozen digest; actual source/artifact/runtime owner stays labeled | first thread-binding slice implemented |

## PCC Design Matrix

| Mechanism | Preconditions and invalidation | Exact fallback | Owner/backend implication | Required evidence | Priority |
|---|---|---|---|---|---|
| Frontend IR action cache | source bytes, compiler identity, semantic options, target, entry graph, and ABI-producing sources all match | regenerate IR | cache format and validation implemented in pcc-Python; host helper is labeled metadata assistance only | hit/miss, hash/load/publish time, bytes avoided, isolated cold/warm stage | P0 current |
| Per-module action DAG and summaries | import/export/type/effect dependency hashes match | invalidate affected reverse dependency closure | common summary consumed by self and LLVM paths | no-op and one-file edit latency, rebuilt module count, fixed point | P1 next |
| Pass analysis manager | every pass declares required/preserved analyses; deletion clears stale results | recompute analysis | owner-neutral pass contract, backend extension points explicit | analysis hit/miss/recompute counts and unchanged semantic corpus | P1 |
| Compact compiler arenas/IDs | profile proves pointer/map traversal or allocation dominates; stable source-span side table exists | existing object representation | pcc-Python implementation first, C kernel unaware | allocation count, peak RSS, cache-miss/sample profile, compile time | P1 |
| Guarded Python specialization | exact type/layout/function/global version and effect set known | generic Python runtime operation | identical guard IR in self and LLVM; no libpython edge | hit/miss/deopt counters and oracle differential tests | P1 |
| Escape/scalar replacement | no identity, weakref, finalizer, traceback, coroutine, extension, thread, or GC-root escape | allocate/materialize object | stack maps and all five GCs consume one materialization contract | allocation/RSS/GC work plus identity/finalizer corpus | P1/P2 |
| Loop/vector cost model | typed value/buffer lane, no unsafe alias/effect, exact overflow/exception policy, profitable target plan | scalar loop with identical order | owner-neutral VPlan-like form lowered by self, LLVM, and Metal as applicable | optimization remarks, IR shape, runtime benchmark, oracle equality | P1/P2 |
| GPU schedule IR | exact Kernel IR digest, target, typed selector, expected prior state, and target limit match | reject the schedule; compile the unchanged semantic module only when explicitly requested without it | one PCC schedule module feeds pcc-metal and the pinned tvm-tilelang adapter before freeze | replay trace/digest, cross-owner frozen digest, source/artifact identity, CPU oracle, real Metal result | P1 current |
| Conservative PGO | representative profile bound to code/mode/target; source match succeeds | ordinary AOT decisions | profile reader and decisions owned by PCC; external profiler is labeled input | stable repeated benchmarks and no degradation gate | P2 |
| Optional runtime tier | long-lived hot region, bounded code cache, safe deopt/materialization | baseline PCC native code | never required for self-host fixed point | startup, steady throughput, RSS, deopt rate | P2 |

## Explicit Rejections

- Do not enable `-ffast-math`, reassociation, fixed-width wraparound for Python
  `int`, or architecture-native code generation as unlabeled defaults.  They
  change results or portability.
- Do not vectorize arbitrary dynamic Python loops without effect, alias,
  exception-order, and overflow proof plus a scalar path.
- Do not make LLVM, CPython, TVM, TileLang, host pip, or a host Python process
  the hidden execution owner.  They may remain explicit oracles, acquisition
  helpers, or build-time metadata tools.
- Do not use package names, GC numbers, test node IDs, fixed module counts, or
  benchmark inputs in compiler policy.
- Do not treat more workers as a universal optimization.  Parallel work must
  be bounded by memory, cache locality, dependency readiness, and measured
  critical-path benefit.
- Do not add a JIT as a substitute for fixing repeated AOT work, compact IR,
  self-backend code generation, or exact Python semantics.

## Proposals

- No.1 Content-address the whole deterministic frontend IR bundle [IMPLEMENTED;
  isolated A/B CONFIRMED]
- No.2 Replace whole-graph invalidation with a per-module action DAG [pending]
- No.3 Add analysis preservation and invalidation to the pass manager [pending]
- No.4 Introduce compact compiler arenas only at measured allocation hot spots [pending]
- No.5 Add guarded specialization and effect-aware loop plans [pending]
- No.6 Add conservative, code-identified PGO and an optional long-running tier [pending]
- No.7 Separate replayable GPU schedules from semantic Kernel IR [CONFIRMED]

## No.1 Content-address the whole deterministic frontend IR bundle

### Code Change

Add a generic content-addressed cache immediately before Python IR passes.  Its
key covers ordered source content, actual compiler executable content, entry
module and sibling ordering, platform/machine, libpython and scaffold modes,
and frontend codegen environment.  Runtime GC selection, worker count,
profiling, object-emitter policy, and post-frontend passes remain outside the
key because they do not change this pre-pass bundle.  Entries contain a
manifest, bundle digest, ordered module names, and per-module character sizes;
corruption is a miss.  Publication uses a lock and atomic rename.

The implementation is in `pcc/py_frontend/compile_cache.py`, with the pipeline
boundary in `pcc/py_frontend/pipeline.py`.  It uses the existing explicit host
tool subprocess boundary for hashing and atomic filesystem metadata so the
compiled pcc1 does not regain a libpython edge.  If the tool is absent, it is a
normal miss and compilation continues.  This is an optimization boundary, not
an execution or semantic fallback.

### Measured result and remaining closure

Focused host tests and strict no-libpython scanning pass.  The isolated
stage2 cache-miss/cache-hit A/B records 373.914 seconds/5.319 GiB versus 38.209
seconds/2.689 GiB, an 89.8% wall-time reduction with a lower RSS peak.  The
compiler key originally included both executable bytes and the executable's
absolute path.  That was not content addressing: the matrix copies one
byte-identical pcc1 into each GC output directory, so path identity split one
reusable action into five cache entries.  Cache schema v2 keys compiler
identity only by SHA-256 while retaining source, target, modes, and semantic
environment; different compiler bytes still miss.  The shared stage1 now enters
the same source-content cache namespace as stage2/stage3, and a real lock/publish/
wait regression proves byte-identical compiler copies coordinate on one bundle.

The forced five-GC fixed-point matrix and both exact suites must still finish
with final summaries inside 900 seconds.  Those gates remain mandatory and are
the closure boundary for the active P0 task.

## No.2 Replace whole-graph invalidation with a per-module action DAG

### Code Change

Serialize compact module summaries containing public types, exports, effects,
layout/ABI dependencies, imports, call edges, and source identity.  Hash each
compile action from its direct inputs and dependency content IDs.  Rebuild only
the reverse dependency closure affected by a source or summary change.  Keep a
whole-graph consistency check at link/fixed-point boundaries.

### Pending

This should follow No.1: a correct coarse cache establishes the semantic key
before the key is decomposed.  Gates must compare no-op, leaf edit, public API
edit, runtime ABI edit, and compiler edit behavior.

## No.3 Add analysis preservation and invalidation to the pass manager

### Code Change

Give analyses stable IDs and scopes (module, SCC, function, loop).  Each
transformation declares required and preserved analyses; the manager caches
valid results and invalidates the rest.  Inner passes may read cached immutable
outer summaries but may not trigger a whole-module rescan.

### Pending

First profile current repeated type/effect/dominator/call-graph analyses and
choose one demonstrably repeated analysis as the tracer slice.

## No.4 Introduce compact compiler arenas only at measured allocation hot spots

### Code Change

Represent hot IR values/blocks and analysis facts by dense integer IDs in
contiguous arenas, with source spans and semantic types stored in side tables.
Segregate pointer-free data where it reduces GC scanning.  Preserve the current
object representation at API/debug boundaries until materialization is needed.

### Pending

Requires allocation and sampling profiles.  It is denied if a benchmark shows
only emitter or subprocess time, because a representation rewrite without a
measured memory/locality bottleneck would add complexity without benefit.

## No.5 Add guarded specialization and effect-aware loop plans

### Code Change

Introduce a shared guard vocabulary (exact type, shape/layout version, no
alias, stride/alignment, integer range, no observable effect) and an explicit
slow-path edge.  Build a small owner-neutral loop plan with separate legality
and target cost decisions.  Emit optimization remarks for both accepted and
rejected plans.

### Pending

Start with a typed value/buffer loop whose scalar PCC semantics already pass an
oracle.  Ordinary dynamic loops remain unchanged.  LLVM and self output must
show equivalent guards and scalar fallback before any performance claim.

## No.6 Add conservative, code-identified PGO and an optional long-running tier

### Code Change

Store branch/type/call/value profiles under a source, semantic-mode, ABI, and
target identity.  Unmatched records are ignored.  Use profiles first for
inlining, layout, and specialization priority.  Only after stable AOT behavior
consider a bounded optional code cache that can deopt/materialize into baseline
PCC native code.

### Pending

This is lower priority than deterministic incremental compilation, compact
compiler data, and guarded AOT specialization.  It must prove steady-state
throughput without unacceptable startup, RSS, fragmentation, or profile-skew
regressions.

## No.7 Separate replayable GPU schedules from semantic Kernel IR

### Code Change

Add `pcc/kernel_ir/schedule.py` as a deep schedule module with one first-slice
instruction, `BindThreads`.  A `KernelSchedule` records its schema, canonical
target, exact input Kernel IR digest, function selector, expected old thread
binding, and new binding.  Applying it returns a new immutable KernelModule and
a deterministic trace; it does not mutate semantic IR.  Stale input, stale
binding, duplicate or missing selectors, target mismatch, invalid Metal thread
counts, and scheduling after plain-TIR freeze fail closed.

Both `PccMetalGpuBackendDriver` and `TvmTilelangGpuBackendDriver` call the same
schedule module before `lower_to_plain_tir`.  The scheduled frozen-IR digest is
therefore identical across owners.  The schedule digest participates in each
compiled artifact identity and appears explicitly in the owner manifest and
artifact hash map.  An unscheduled call preserves the existing interface and
behavior.

### CONFIRMED

The focused schedule and existing owner gates pass `14 passed`; the real pinned
TileLang/TVM provider gate passes `3 passed, 5 deselected`; and the strict
Darwin Metal scheduled-copy gate reaches a real device result with `1 passed,
14 deselected`.  The CPU copy oracle remains identical, requested and actual
owner are both `pcc-metal`, and `fallback_used=false`.

This confirms only the owner-neutral Metal thread-binding instruction.  It
does not claim general schedule IR, tiling, layout transformation, software
pipelining, autotuning, CUDA/ROCm, or whole-program GPU execution.

## Update 2026-08-02: primary-source closure and finite roadmap

The earlier draft identified the right design families but did not yet make
every recommendation falsifiable.  This update closes that gap.  It also
records that Proposal No.1's formerly pending repository gates were completed
on one unchanged darwin-arm64 tree: the forced five-GC matrix passed in
363.17s, the exact non-integration suite passed in 756.78s, and the exact
integration suite passed in 786.99s.  The detailed commands and mode labels are
preserved in
[`2026-07-31-bootstrap-phase-reuse-criterion4-closure.md`](../goal/evidence/2026-07-31-bootstrap-phase-reuse-criterion4-closure.md).

### Primary-source coverage

The following table is the source-to-claim map for the requested Cornell
categories.  Cornell's material is guidance for finding profitable work, not
evidence that a PCC transformation is legal or fast; each PCC row below still
requires its own semantic and benchmark gates.

| Requested category | Primary Cornell material | Claim used by PCC |
|---|---|---|
| Measurement | [Profiling](https://cvw.cac.cornell.edu/python-performance/assessment/profiling) | Optimize measured call/time hot spots; counters and repeatable workloads precede implementation. |
| Algorithms | [Rough Tuning](https://cvw.cac.cornell.edu/code-optimization/coding-for-performance/rough-tuning) and [Scalable Algorithms](https://cvw.cac.cornell.edu/scalability/planning-for-parallel/scalable-algorithms) | Algorithm and dependency structure dominate instruction tuning; useful tasks expose independent work and limit communication. |
| Memory and locality | [Memory Hierarchy](https://cvw.cac.cornell.edu/code-optimization/single-core-optimization/memory-hierarchy), [Memory Access Times](https://cvw.cac.cornell.edu/code-optimization/single-core-optimization/memory-access-times), and [Array Blocking](https://cvw.cac.cornell.edu/code-optimization/cache-considerations/array-blocking) | Compact traversal and reuse matter only when allocation, RSS, or locality samples identify the bottleneck. |
| Loops | [Loop-Invariant Code Motion](https://cvw.cac.cornell.edu/code-optimization/data-locality/code-motion) | Hoisting is legal only for proven invariant and non-observable operations. |
| Calls | [Best Practices for Compilers](https://cvw.cac.cornell.edu/code-optimization/opt-via-compilers/best-practice-compilers) | Hot small calls are inlining candidates, but PCC must first prove the callee, effects, ownership, and exception behavior. |
| Vectorization | [SIMD and Micro-Parallelism](https://cvw.cac.cornell.edu/code-optimization/single-core-optimization/simd-micro-parallelism), [Vectorizable Code](https://cvw.cac.cornell.edu/vector/coding/vectorizable-code), [Data Dependencies](https://cvw.cac.cornell.edu/vector/coding/data-dependencies), and [Optimization Reports](https://cvw.cac.cornell.edu/vector/compilers/optimization-reports) | Widen only countable, independent operations; report both accepted and rejected plans and retain an exact scalar path. |
| Parallelism | [Multi-Core Cache Sharing](https://cvw.cac.cornell.edu/code-optimization/cache-considerations/multicore-cache-sharing) and [Scaling](https://cvw.cac.cornell.edu/parallel/efficiency/scaling) | Worker count is a measured resource decision; cache sharing, serial work, memory bandwidth, and problem size bound useful speedup. |

The non-Cornell comparisons use primary or owner-maintained material: CPython
[PEP 659](https://peps.python.org/pep-0659/) and the official
[Python 3.13 JIT notes](https://docs.python.org/3.13/whatsnew/3.13.html#an-experimental-just-in-time-jit-compiler);
Go's [`buildid.go`](https://go.dev/src/cmd/go/internal/work/buildid.go),
[compiler overview](https://go.dev/src/cmd/compile/README),
[SSA design](https://go.dev/src/cmd/compile/internal/ssa/README), and
[PGO design](https://go.dev/doc/pgo) (the inspected local Go source is pinned at
`a961f702a48edbfc044639775f4ffae692b7f0dc`); Oracle's
[HotSpot tiering/escape-analysis documentation](https://docs.oracle.com/en/java/javase/17/vm/java-hotspot-virtual-machine-performance-enhancements.html)
and OpenJDK [JEP 197](https://openjdk.org/jeps/197); GraalVM's
[compiler pipeline](https://www.graalvm.org/jdk21/reference-manual/java/compiler/);
and LLVM's [new pass manager](https://releases.llvm.org/15.0.0/docs/NewPassManager.html),
[ThinLTO](https://clang.llvm.org/docs/ThinLTO.html), and
[vectorizer](https://llvm.org/docs/Vectorizers.html) documentation.  These
sources support architecture choices; they do not transfer their benchmark
results to PCC.

### Authoritative closure matrix

This matrix supersedes the shorter draft matrix above for roadmap decisions.
Every accepted optimization names its legality/invalidation boundary, exact
fallback, execution owner, expected counter, and a benchmark that can reject
the proposal.

| Mechanism and classification | Semantic precondition and invalidation/deopt | Exact fallback | Owner/backend consequence | Expected counters | Falsifiable gate |
|---|---|---|---|---|---|
| Frontend IR content cache — **implemented now** | All source, compiler-byte, semantic-mode, target, entry-graph, and ABI-producing identities match; corruption or any mismatch is a miss. | Regenerate the full deterministic frontend bundle. | Keying/validation are PCC-owned and common to LLVM/self; a labeled host metadata subprocess is not an execution fallback. | `lookup_s`, `hit`, `miss_reason`, `bytes_loaded`, `bytes_avoided`, `publish_s`. | An isolated same-input repeat must preserve fixed-point bytes and reduce dominant stage wall time; five-GC and both exact suites must finish below 900s. This passed in the linked criterion-4 evidence. |
| Per-module action DAG/summaries — **generic next slice** | Source plus imported public type/export/effect/layout summaries match; a private implementation edit must not masquerade as a public-summary match. | Rebuild the affected reverse-dependency closure, or the full graph if a summary/ABI cannot be validated. | One summary schema feeds LLVM and self; no package, module-count, or test-name policy. | `actions_total`, `actions_hit`, `actions_rebuilt`, `reverse_closure_size`, `summary_bytes`, per-action wall/RSS. | No-op rebuild must compile zero modules; private leaf edit only its action; public export edit exactly the reverse closure; compiler/runtime ABI edit all actions; results and pcc2/pcc3 fixed point remain equal. Task `PERF-P1-INCREMENTAL-MODULE-ACTION-DAG`. |
| Pass analysis preservation/invalidation — **generic next slice** | Each pass declares scope, requirements, preserved analyses, and mutations; a deletion or unknown mutation invalidates conservatively. | Recompute the analysis or run the existing uncached pass pipeline. | Owner-neutral manager; backend-only analyses are explicitly named extensions and cannot hide an LLVM run in self mode. | Per-analysis `query`, `hit`, `miss`, `invalidate`, `recompute_s`; pass wall and peak RSS. | A profile-selected repeated analysis must show fewer recomputations and lower pass time without semantic-oracle, fallback-ratchet, or bootstrap drift; an injected mutation must force a miss. Task `PERF-P1-PASS-ANALYSIS-INVALIDATION`. |
| Compact compiler arenas/dense IDs — **profile-required** | Sampling/allocation evidence names a pointer/map traversal hot spot; stable source-span/type side tables preserve diagnostics and object projection. | Existing object representation and debug materialization. | Implemented in pcc-Python compiler data first; the C kernel and Python object semantics do not change. | Allocation count/bytes by type, materializations, arena bytes, scan time, phase RSS, sample share. | The chosen phase must reduce its profiled allocation bytes by at least 10% or wall time by at least 5%, with no total-RSS regression above 2% and byte-identical output; otherwise reject/revert the representation. Task `PERF-P1-COMPACT-COMPILER-ARENAS`. |
| Guarded specialization plus loop/vector plan — **value-model/proof required** | Exact type/layout/function/global versions, effects, alias/stride/alignment, integer range, exception order, and target profitability are proved or guarded. Any guard miss invalidates only the fast edge. | The existing generic operation or ordered scalar loop, including arbitrary-precision promotion and exceptions. | One PCC-owned guard/loop-plan vocabulary lowers separately in self and LLVM; Metal consumes it only after an explicit Kernel IR boundary. | Candidate/accepted/rejected plans by reason, guard hits/misses, scalar fallbacks, overflow promotions, vector width, fast/slow time. | A typed value/buffer workload must show equivalent self/LLVM guards, allocation-free accepted IR, oracle equality on hit/miss/overflow/alias cases, and a statistically repeatable speedup; any semantic mismatch or absent speedup denies that plan. Task `PERF-P1-GUARDED-SPECIALIZATION-LOOP-PLAN`. |
| Escape/scalar replacement — **deferred value-model slice** | No identity, `__dict__`, weakref, finalizer, traceback, suspended-frame, extension, thread, native-handle, or GC-root escape; every observation point can materialize exactly once. | Allocate/materialize the ordinary object under the normal ownership and five-GC trace/update contract. | Self/LLVM share escape facts and materialization ABI; all five GCs see the same slots and roots. | Candidates, rejected escape reasons, eliminated allocations, materializations, GC slot visits, RSS/pause/throughput. | A focused value payload must eliminate allocations while every forced escape preserves `id`/`is`/weakref/finalizer/exception behavior under GC0..4; no backend may rely on LLVM-only escape analysis. Task `PERF-P2-ESCAPE-SCALAR-MATERIALIZATION`. |
| Replayable GPU schedule — **implemented narrow slice** | Exact semantic IR digest, target, selector, prior binding, and target limits match; stale or post-freeze schedules fail closed. | Reject the requested schedule; an explicitly unscheduled compile retains the semantic module. | One PCC schedule record feeds pcc-metal and labeled tvm-tilelang before freeze; actual owner remains recorded. | Legality rejection reasons, schedule/frozen digests, owner/fallback labels, compile/runtime/device counters. | Cross-owner frozen digest and CPU result must match, and strict pcc-metal must reach a real device result with `fallback_used=false`; the existing thread-binding gates passed. |
| Conservative code-identified PGO — **lower-priority generic slice** | Representative profile matches source/code identity, semantic mode, ABI, target and schema; stale/unmatched samples are ignored. | Ordinary deterministic AOT decisions. | PCC owns profile matching and decisions; external profilers are labeled inputs and self-host never requires a profile. | Matched/unmatched samples, profile age/skew, decisions by kind, code-size/build-time deltas, workload counters. | Across pinned representative and adversarial-skew workloads, PGO must improve the target metric without statistically significant regression beyond 2% in no-PGO workloads, semantic drift, or fixed-point dependence; otherwise it stays off. Task `PERF-P2-CODE-IDENTIFIED-PGO`. |
| Optional long-running tier — **deferred until AOT/PGO gates** | A long-lived region crosses explicit heat/cost thresholds; code cache and deopt metadata are bounded; observation can materialize baseline state. | Baseline PCC native AOT code. | Tier is optional, PCC-owned, mode-labeled, and never needed for pcc1/pcc2/pcc3 or native artifact validity. | Compile queue/time, tier entries/exits/deopts, code-cache live/dead/fragmentation, startup, steady throughput, RSS/pause. | A long-running matrix must improve steady throughput while startup, peak RSS, fragmentation, and p99 pause remain within task budgets; a forced-deopt corpus must equal baseline output. Failure leaves tier disabled. Task `PERF-P2-OPTIONAL-RUNTIME-TIER`. |

### Rejected or explicitly deferred transfers

- CPython quickened bytecode, HotSpot/Graal tiering, and LLVM's optimizer may
  inform PCC, but none may become the hidden owner of `--backend=self` or a
  prerequisite for the self-host fixed point.
- Java primitive overflow and `-ffast-math` are incompatible with Python
  arbitrary-precision `int`, ordered exceptions, and default floating-point
  semantics.  Only explicit `pcc.i64`/`pcc.u64` or a guarded proven range can
  use machine arithmetic.
- LLVM vectorization of a dynamic Python loop without an owner-neutral legality
  plan is rejected even if it benchmarks well: the self backend, scalar slow
  path, exception order, and alias behavior would be unproven.
- Whole-program GPU ownership, ambient-hardware schedules, unbounded autotuning,
  and package-specific library dispatch remain outside the accepted slices.

### Finite task-board mapping

Proposal No.1 is closed by `PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE`, and No.7 by
`GPU-P1-OWNER-SCHEDULE-THREAD-BINDING`.  The remaining accepted work is split
into seven independently gated rows rather than one unbounded optimization
epic:

1. `PERF-P1-INCREMENTAL-MODULE-ACTION-DAG`
2. `PERF-P1-PASS-ANALYSIS-INVALIDATION`
3. `PERF-P1-COMPACT-COMPILER-ARENAS`
4. `PERF-P1-GUARDED-SPECIALIZATION-LOOP-PLAN`
5. `PERF-P2-ESCAPE-SCALAR-MATERIALIZATION`
6. `PERF-P2-CODE-IDENTIFIED-PGO`
7. `PERF-P2-OPTIONAL-RUNTIME-TIER`

## Report

The audit is resolved as a design and routing task, not as a claim that all
listed optimizations are implemented.  Primary sources support the mechanism
shapes; PCC's own counters and gates decide whether each is legal and useful.
Two proposals have implementation evidence: content-addressed frontend reuse
and the narrow replayable Metal thread-binding schedule.  The seven remaining
slices are now finite task-board rows with explicit slow paths, ownership
labels, counters, rejection thresholds, correctness gates, fixed-point gates,
and memory/performance gates.  No package, GC number, benchmark input, module
count, or test name is compiler policy, and no oracle was relabeled as a PCC
owner.
