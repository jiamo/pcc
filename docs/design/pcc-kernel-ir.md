# PCC Kernel IR — Design

Status: `pcc/kernel_ir/` is a self-contained, importable package with a strict
kernel-only boundary. The first slice was metadata/golden-only. The current
artifact slice adds an explicit opt-in Metal source artifact path plus CPU
host-launch-boundary proof for the first global-buffer copy/fill-shaped subset.
The current LocalBuffer slice also separates device-local storage from host
launch parameters: threadgroup scratch is modeled as a kernel-body allocation,
not a hidden host argument. The current reduction slice adds a bounded
threadgroup sum source lowering, and the current fragment slice adds
thread-private fragment/local copy/fill source lowering.
It still does **not** claim whole-program GPU execution. AIR/metallib production
is wired through the existing Xcode Metal CLI helpers, but it remains
`SKIPPED_WITH_REASON` on machines where the Metal Toolchain component cannot
execute. A separate runtime-source bridge can compile Metal source with
`newLibraryWithSource`; the first real local probe submitted a copy-kernel
command buffer, completed a fence, read back native output, and matched the CPU
oracle. The simdgroup slice adds an opt-in 8x8 f16/f16->f32 Metal simdgroup
GEMM microkernel source path with runtime-source execution and CPU-oracle proof,
including row/column tile swizzle, nonzero K ranges, divisible split-k atomic
accumulation, and strict static transposed operand layouts.
These are executed runtime-source launch claims only, not `.metallib`
production claims.

This document is the authoritative map for the `K-P0-*` cluster. The broader
GPU / GPU-GC / distributed / ds4 roadmap and claim-level policy lives in
`docs/design/pcc-gpu-next-work.md`. Together they let the next agent extend the
pipeline without re-reading the TVM/TileLang research report
(`docs/refs_docs/deep-research/deep-research-tvm.md`) or the local reference trees.

## 1. Why a kernel-only IR at all

pcc's north star (`AGENTS.md`) is *Python execution ownership*: compiled,
inspectable, self-hostable, package-aware, honest about every fallback boundary.
A GPU/tile lowering path threatens that in exactly one way — it is tempting to
hand pcc's object graph, GC, exceptions, and finalizers to an external IR
(TVM/TIRx) or an external runtime (TileLang) and let *them* own semantics.

The research report's central judgment is: **do not integrate whole-TVM /
whole-TileLang.** Instead keep pcc's language + runtime sovereignty, use a
TIRx-*compatible* lowering layer as a device-kernel middle tier, and freeze tile
semantics into *plain TIR* before any target codegen. The mechanism that makes
this safe is a **kernel-only IR boundary**: a strict subset that can only carry
things a GPU kernel is legally allowed to see.

That boundary is `pcc/kernel_ir/ir.py`. Its validator (`validate_kernel`)
**raises** the moment a kernel parameter or body references a
`list`/`dict`/`PyObject`/weakref/finalizer/GC-frame escape. This is not a lint —
it is the enforcement point for obligation "kernel IR only sees handle +
metadata, never `PyObject*`".

## 2. The pipeline

```text
Python AST
  -> PCC HIR                     (owned by pcc; GC/exceptions/objects live here)
  -> Kernel Region Extraction    (pull out the explicitly-marked kernel subgraph)
  -> PCC Kernel IR               (ir.py: POD scalars + buffer handles + layout +
                                  thread/memory scope + fence; escapes REJECTED)
  -> TIRx-compatible IR          (tirx_adapter.py: TilePrimitiveDispatch-style
                                  freeze of copy/fill/parallel/gemm)
  -> plain TIR                   (the `plain_tir_freeze` marker: tile primitives,
                                  TileLayout, and scope-ids are gone here)
  -> Split Host / Device         (target_split.py: the backend organization root)
       -> Host Finalize          (self | llvm | c) -- pcc keeps host sovereignty
       -> Device Finalize        (metal | none)    -- metal_finalize.py, descriptors only
```

`plain_tir_freeze` is the crucial **intermediate freeze surface**: after it, tile
primitives / `TileLayout` / execution-scope ids are gone and the module is plain
TIR. This is where "tile semantics processing" ends and "backend engineering"
begins — exactly the seam the research report says is stable and reusable.

## 3. Module map

| Module | Row | Role | State |
|---|---|---|---|
| `ir.py` | K-P0-TVM-KERNEL-IR | Kernel-only IR types + escape-rejecting validator + golden dump | complete |
| `tirx_adapter.py` | K-P0-TIRX-ADAPTER | Freeze tile semantics into plain TIR (`plain_tir_freeze`); CUDA-only assumption rejected for Metal | complete |
| `target_split.py` | K-P0-TARGET-SPLIT | TargetMachine registry: `host=self\|llvm\|c` + `device=metal\|none`; **no self->LLVM fallback**; no device finalize during host-only | complete |
| `hmm_fence.py` | K-P0-TVM-HMM-FENCE | `PccBufferHandle`/`PccPackedArgs`/`PccFenceToken`, deferred-free queue, DLPack/POD arg validation; free delayed until fence completes | complete |
| `tilelang_compat.py` | K-P0-TILELANG-COMPAT | Parse/inspect-only accepted/rejected construct matrix for the TileLang subset | complete |
| `tilelang_import.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | Strict AST importer for the first TileLang Metal matmul Python-DSL shape plus split-k-shaped 3-D grid metadata, positional GEMM transpose metadata, one-extent serial K-loop metadata, T.Parallel extent/name metadata, one-dimensional T.vectorized metadata, disabled/enabled row-column T.use_swizzle metadata, empty T.annotate_layout metadata, and swizzled/padded T.annotate_layout local-buffer metadata into pcc Kernel IR; no TileLang/TVM execution | weak: one static matmul-shaped subset plus first schedule-metadata slices only |
| `../gpu_kernel.py` | GPU-P0-CANONICAL-KERNEL-IR-PATH | `@gpu.kernel` imports vector add plus the finite scalar/indexed/if subset into validated Kernel IR before TIRx/Metal; unsupported syntax fails closed and no direct AST-to-Metal route exists | strong for this finite frontend subset; broader syntax requires new Kernel IR semantics first |
| `cpu_reference.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | Numeric CPU reference oracle for the frozen scalar tiled GEMM subset, including edge tiles, K-tail validation, strict static transpose_A/transpose_B layouts, legal T.Parallel/T.vectorized A/B/C tile-copy staging metadata, disabled/enabled row-column T.use_swizzle metadata, empty T.annotate_layout no-op metadata, and semantic execution when local swizzled-layout metadata is present | weak: current scalar tiled GEMM subset only, no GPU execution |
| `metal_finalize.py` | K-P0-METAL-TVM-FINALIZE | Metal finalize descriptors plus opt-in `.metal` source artifact emission, threadgroup-local and thread-private fragment/local declarations for the supported copy/fill and bounded sum-reduction subsets, scalar tiled GEMM source for the imported TileLang matmul shape including static transpose_A/transpose_B layouts, legal T.Parallel/T.vectorized A/B/C tile-copy staging metadata, TileLang row/column T.use_swizzle tile rasterization for scalar GEMM plus opt-in simdgroup GEMM, empty T.annotate_layout no-op metadata, TileLang-compatible rank-2 bank-swizzled and padded shared A/B layout indexing, opt-in 8x8 simdgroup GEMM microkernel source, and wired `.air`/`.metallib` compilation path | weak: first global-buffer/threadgroup-local/thread-private-fragment/reduction/scalar-GEMM and 8x8 simdgroup-GEMM source subsets only |
| `host_device_split.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | CPU host-launch-boundary proof: host backend, Metal device target, launcher symbols, POD/buffer bindings, separate device-local bindings, no whole-program GPU claim | weak: boundary proof only, no runtime launch |
| `metal_launch.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | Runtime-facing launch packet validation, command-encoder plan over `PccPackedArgs`/buffer handles, Objective-C executor bridge source emission, and optional host `.o` bridge artifact production; execute requests skip honestly until an executor exists | weak: plan + bridge source/object artifact only, no command-buffer commit |
| `metal_buffer.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | Native Metal buffer binding proof surface keyed by logical `PccBufferHandle.handle_id`, plus Objective-C create/length/release/write/read runtime bridge for native `id<MTLBuffer>` allocation, host byte transfer, and launch-plan binding | weak: buffer allocation + host byte roundtrip only, no tensor/DLPack integration or command-buffer launch |
| `metal_invoke.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | Strict host bridge invocation wrapper for ready packets; refuses missing metallib/native buffers/unsafe async callback lifetime and distinguishes injected ABI validation from real bridge calls | weak: invocation boundary only, no successful command-buffer execution proof |
| `metal_source_runtime.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | Runtime-source Objective-C bridge path using `newLibraryWithSource`, native MTLBuffer bindings, POD scalar packing, pcc fence callback, first-class package/runtime result, command-buffer submit, readback, and CPU-oracle comparison for the first copy-kernel, imported TileLang scalar-GEMM, and 8x8 simdgroup-GEMM proofs | weak: runtime-source copy plus small imported TileLang scalar GEMM plus 8x8 simdgroup-GEMM proof only; not `.metallib` |
| `metal_tensor.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | Typed row-major matrix marshalling between CPU-oracle-shaped host data and native MTLBuffers using launch-plan dtype/shape metadata | weak: host matrix data plane only, no DLPack/tensor ownership or device-computed output |
| `metal_dlpack.py` | GPU-P0-DLPACK-EXTERNAL-CAPSULE-INTEROP | Classic C-ABI `DLManagedTensor`/`DLTensor`/`DLDevice`/`DLDataType` PyCapsule export and foreign import over native MTLBuffer pointers, one-shot consume, contiguous/default-stream validation, POD handle re-entry, and fence-deferred pcc/foreign deleters | strong for classic contiguous/default-stream ABI; no torch/MLX/MPS, non-default stream, pcc1, or five-GC device claim |
| `metal_verify.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | Native output readback comparison against `CpuReferenceResult` with tolerance and coordinate-level mismatch diagnostics | weak: verifier path plus runtime-source copy and small imported TileLang scalar GEMM output proofs only |
| `metal_package.py` | GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT | Non-executing package manifest tying CPU oracle, Metal finalize/source artifact, launch plan, bridge source/object/dylib artifacts, bridge dylib load/symbol validation, native-buffer binding readiness, bridge invocation ABI packet shape, and deterministic JSON + artifact hash verification together | weak: proof bundle/integrity/load-symbol/binding/packet-shape only, no runtime launch |
| `tvm_oracle.py` | K-P0-TVM-CXX-ORACLE | One bounded TVM seam (TIR-style golden serialization shape) for comparison | complete (one seam) |

All modules import standalone (`import pcc.kernel_ir.ir`) and do **not** touch
`pcc/__init__.py`. `pcc/kernel_ir/__init__.py` re-exports the public names.

## 4. The hard claim boundary (never weakened)

```text
1. NEVER whole-Python-on-GPU. Only an explicitly-marked, statically-constrained
   kernel subset (POD scalars + buffer handles + layout + scope + fence) crosses
   the device frontier. validate_kernel() RAISES on any list/dict/PyObject/
   weakref/finalizer/GC-frame escape.
2. NEVER GC-managed objects in device IR. The kernel IR sees PccBufferHandle +
   metadata, never a PyObject*. GC owns host objects + launcher stubs; HMM owns
   buffer handles, device allocations, fence tokens, queue resources.
3. LLVM is ORACLE, not OWNER. --backend=self resolves to the self host backend
   with NO silent fallback to LLVM (target_split enforces this; a resolution
   that would fall back RAISES). Self-backend is a first-class execution root.
4. fence is the causal edge for resource release. A buffer's underlying storage
   is freed only AFTER its PccFenceToken completes — never at the moment a
   Python wrapper is collected.
5. Artifact-producing APIs must label exactly what was produced. `.metal`
  source production, host bridge `.m/.o/.dylib` production, bridge symbol
  validation, bridge invocation ABI packet shape, `.air/.metallib` production,
  host-launch-boundary proof, and executed GPU launch are separate claims. The
  current implementation proves `.metal` source plus host-launch boundary for
  the first subset, runtime-source command-buffer execution for selected
  kernels, and host bridge artifact/packet-shape production; it does not claim
  `.metallib`-backed execution or speedup.
```

## 5. What is complete vs stubbed in this slice

Complete (real invariants, enforced + tested):
- escape rejection actually raises (`ir.validate_kernel`)
- no self->LLVM fallback rule enforced (`target_split.resolve`)
- fence delays free (`hmm_fence` state machine)
- golden round-trips (`ir` dump, `tirx_adapter` freeze, `tvm_oracle` shape)
- device-local `LocalBuffer` is preserved through TIRx freeze, projected as a
  TIR-style `alloc_buffer`, and excluded from CPU host launch args
- TileLang accepted/rejected construct matrix (`tilelang_compat`)
- strict TileLang Metal matmul Python-DSL AST import preserves tensor shapes,
  launch grid/thread count, device-local shared/fragment allocations, and
  clear/copy/gemm/Pipelined ops without executing TileLang/TVM
- broader TileLang schedule-metadata import for split-k-shaped 3-D
  `T.Kernel(...)` grids and positional
  `T.gemm(A, B, C, transpose_A, transpose_B)` attrs; strict static
  `transpose_A`, `transpose_B`, and combined transpose scalar tiled layouts
  execute through the CPU oracle and Metal source/runtime-source paths
- one-extent `T.serial(...)` K-loop import and two-argument
  `T.serial(start, end)` / `T.Pipelined(start, end, ...)` import for the
  current copy/gemm body subset, preserving start/extent metadata through TIRx
  freeze and validating/executing it in the CPU oracle and Metal source/runtime
  paths, including nonzero K-loop starts
- `T.Parallel(...)` and one-dimensional `T.vectorized(...)` loop metadata
  import for the current nested copy/gemm body subset, preserving extents and
  target variable names through TIRx freeze; CPU oracle and Metal source
  lowering accept only the legal A/B global-to-shared and C
  fragment/local-to-global tile-copy staging forms when scheduled extents match
  the relevant tile shape, and runtime-source execution for those forms matches
  the CPU oracle
- `T.use_swizzle(...)` import/freeze/runtime-source proof as explicit
  rasterization metadata; disabled swizzle is a no-op, enabled row/column
  swizzle remaps scalar-GEMM threadblock ids using TileLang's 2-D
  rasterization formula, and enabled row/column swizzle is also proven for the
  current opt-in 8x8 simdgroup GEMM runtime-source path
- empty `T.annotate_layout({})` import/freeze/runtime-source proof as explicit
  no-op layout metadata
- swizzled `T.annotate_layout({buf: make_swizzled_layout(buf)})` import/freeze
  proof as shared-local layout metadata visible in TIRx/TVM projection, plus
  runtime-source Metal execution for rank-2 bank-swizzled shared A/B tiles in
  the current scalar GEMM subset; the padded fallback is covered for current
  rank-2 shared A/B scalar-GEMM tiles, while arbitrary layout functions and
  TMA/wgmma layouts remain open
- split-k f32 `T.atomic_add(C, C_local)` output accumulation for the current
  scalar GEMM subset, including non-divisible K tails when the A/B copy-index
  span is explicitly `T.ceildiv(K, split_k)`; floor-div non-divisible
  semantics still fail closed
- numeric CPU reference execution for the frozen scalar tiled GEMM subset,
  including shape/scope/grid/pipeline validation plus edge-tile and K-tail
  behavior against a Python matmul oracle
- Metal finalize descriptors + opt-in `.metal` source artifact emission
- CPU host-launch-boundary proof for global-buffer copy/fill-shaped kernels,
  including threadgroup-local copy/fill staging
- bounded threadgroup `sum` reduction source lowering with explicit
  threadgroup barriers and one output per threadgroup
- thread-private fragment/local copy/fill source lowering using explicit Metal
  `thread` storage
- scalar tiled Metal GEMM source lowering for the imported TileLang Metal matmul
  shape, with static tensor/local shape validation, explicit threadgroup A/B
  tiles, transpose_A/transpose_B index math, uniform barriers, and bounds guards
- opt-in 8x8 Metal simdgroup GEMM microkernel source lowering using
  `simdgroup_half8x8`, `simdgroup_float8x8`,
  `make_filled_simdgroup_matrix`, `simdgroup_load`,
  `simdgroup_multiply_accumulate`, and `simdgroup_store`, while preserving the
  scalar tiled GEMM path as the default fallback; enabled row/column
  `T.use_swizzle` tile-id remap, nonzero K-loop ranges, divisible split-k
  atomic f32 output accumulation plus non-8-wide/ceildiv split-k K tails,
  combined `transpose_A` / `transpose_B` operand layouts, and non-atomic M/N
  edge-tile plus K-tail predication are covered for this current simdgroup
  source/runtime path
- Metal launch packet validation over HMM buffer handles/POD scalars, including
  device/dtype/static-nbytes checks, dispatchThreadgroups shape, command-encoder
  step recording, and explicit `SKIPPED_WITH_REASON` for execution requests
  until a real executor exists
- source-only Objective-C executor bridge emission from the launch plan, mapping
  validated buffers/scalars and dispatch shape to the real Metal API calls and a
  command-buffer completion handler hook for pcc fences
- optional host Objective-C executor bridge `.o` artifact production from the
  emitted bridge source, with toolchain-unavailable skip and compiler-rejection
  error boundaries
- non-executing Metal kernel package manifest tying CPU oracle, Metal source
  artifact, launch plan, and bridge artifact into one proof bundle
- deterministic JSON package manifest with SHA-256/byte-size verification for
  produced package artifacts
- optional host Objective-C executor bridge `.dylib` artifact production from
  the bridge object, recorded and hashed in the package manifest
- optional host `dlopen`/`dlsym` validation that the bridge dylib exports the
  generated bridge symbol, without calling it
- native Metal buffer binding-set validation keyed by logical
  `PccBufferHandle.handle_id`, with exact handle-set matching and non-zero
  `id<MTLBuffer>` pointer requirements
- native MTLBuffer runtime bridge source/object/dylib/load validation for
  create/length/release/write/read helpers, plus smoke-call coverage that
  allocates, length-checks, and releases a native buffer without dispatching work
- launch-plan native MTLBuffer allocation and binding-set construction for every
  buffer arg, with explicit release ownership and failure-path release coverage
- host byte write/read roundtrip through a shared native MTLBuffer, with no
  command queue, command buffer, encoder, dispatch, or fence completion
- typed row-major matrix write/readback using launch-plan dtype/shape metadata,
  including zero-fill of explicit unprovided output buffers and wrong-shape /
  released-allocation rejection
- DLPack-shaped Metal tensor ownership for pcc-managed native MTLBuffer
  allocations, including one-shot consume/import, POD `PccBufferHandle`
  re-entry, alias-counted deleters, per-handle release, and native release only
  after the guarding `PccFenceToken` completes
- classic external `DLManagedTensor` C-ABI capsule export/import, including
  foreign kDLMetal producers and foreign deleter invocation only after the
  guarding fence completes
- native output readback comparison against the CPU oracle with explicit
  tolerance, output-name disambiguation, shape checks, and coordinate-level
  mismatch diagnostics
- non-executing bridge invocation ABI packet recording the sidecar metallib
  path, native buffer pointer slots when supplied, scalar pointer slots, fence
  callback requirement, wait flag, resolved bridge symbol, and not-ready reasons
- strict host bridge invocation wrapper that calls only ready packets, requires
  managed fence completion with `wait_until_completed=True`, treats injected
  CDLL calls as ABI validation only, and records non-zero real bridge returns as
  launch failure rather than execution
- runtime-source bridge emission/build/load/invocation using
  `newLibraryWithSource`, native MTLBuffer bindings, POD scalar packing, and a
  pcc fence callback
- real runtime-source copy-kernel command-buffer submit on local Metal:
  completed fence, read back shaped f32 output, and matched the CPU oracle
- first-class runtime-source package API that ties non-executing package
  artifacts, native-buffer runtime, source-runtime bridge, matrix write,
  command-buffer invoke, CPU-oracle readback comparison, and allocation release
  into one claim-scoped result
- real runtime-source 8x8 simdgroup GEMM command-buffer submit on local Metal:
  completed fence, read back f32 output, and matched the CPU oracle for the
  f16/f16 input microkernel, including enabled row/column `T.use_swizzle` tile
  remap on 2x2 and 3x2 tile grids plus a nonzero K-loop range over the second
  8-wide K tile and divisible split-k atomic f32 output accumulation via
  `tgid.z`, non-8-wide split-k atomic K spans and explicit ceildiv split-k
  tails using zero-padded threadgroup A/B staging, combined transposed A(K,M)
  and B(N,K) operand layouts using transposed `simdgroup_load` source pointers
  and strides, plus non-atomic M/N edge-tile and K-tail execution using
  zero-padded threadgroup staging and bounds-checked C writeback, plus
  split-k atomic M/N edge tiles including the combined M/N-edge and explicit
  ceildiv K-tail case, the first two-simdgroup-per-threadgroup N- and
  M-direction runtime-source shapes (`block_n=16` or `block_m=16`,
  `threads=64`), plus the first four-simdgroup 2-D runtime-source shape
  (`block_m=16`, `block_n=16`, `threads=128`), including the combined
  `transpose_A` / `transpose_B` operand-layout variant
- `SKIPPED_WITH_REASON` when AIR/metallib compilation cannot run

Explicitly out of scope for this slice (honest TODO markers in-code):
- broader simdgroup/tensorcore GEMM lowering beyond the current 8x8
  microkernel slice: larger TileLang block tiling, more-than-four simdgroup
  tiling, multiple simdgroups combined with edge/tail/atomic variants,
  performance proof, and metallib-backed launch
- CPU reference coverage beyond the current scalar tiled GEMM subset
- TileLang import beyond the first static AST subset, including arbitrary
  TileLang/TIRx passes, general executable T.Parallel/T.vectorized loop bodies,
  arbitrary nested/multi-argument loop forms, arbitrary atomics/non-f32
  atomics/floor-div non-divisible split-K semantics and arbitrary split-K index
  expressions, arbitrary/cluster-aware use_swizzle placement, arbitrary layout functions,
  TMA/wgmma shared-memory layouts,
  runtime object/FFI execution, and dynamic Python
  control flow
- metallib-backed host launch, torch/MLX/MPS DLPack round-trip, non-default
  stream synchronization, and pcc1/five-GC cross-runtime ownership handoff
  (classic contiguous/default-stream DLManagedTensor ABI is proven)
- package manifest use as a scheduler/runtime; it is only a proof bundle
- manifest hash verification as device-execution evidence; it proves only local
  artifact integrity
- treating the bridge invocation packet as callable while `metallib_available`
  is false or while `PccBufferHandle` has no native `id<MTLBuffer>` binding
- successful bridge invocation with a real produced metallib
- broader TileLang/GEMM device-computed matrix readback beyond the current
  small runtime-source scalar-GEMM, transposed scalar-GEMM, and legal A/B/C
  T.Parallel/T.vectorized tile-copy staging proofs
- proven `.air/.metallib` production on machines whose Metal Toolchain component
  cannot execute
- CuTeDSL, Hopper/Blackwell/TMA intrinsics, full TileLang runtime + pass pipeline
- the CUDA/HIP device finalize path (only `metal`/`none` are modeled here)

## 6. Test oracle

Gate command (main runs this; this agent does not run pytest):

```bash
env -u LC_ALL uv run pytest tests/kernel -q -n0
```

Each test asserts a *real* invariant, not a shape that merely looks similar:
escape rejection raises, the no-fallback rule raises, fence delays free, goldens
round-trip byte-for-byte, the first TIRx/Metal subset emits a real `.metal`
source artifact plus a CPU host-launch-boundary proof, and AIR/metallib
compilation degrades to `SKIPPED_WITH_REASON` rather than pretending to have
produced a `.metallib` when the local Metal Toolchain cannot execute.

## 7. TileLang claim labels

`pcc/kernel_ir/tilelang_import.py` parses a strict TileLang Python-DSL subset
into pcc Kernel IR. This is **not** a runtime `import tilelang`. Two separate
labels keep the distinction honest, and this module provides only the second:

- `tilelang-package-cpython-compat` — a runtime `import tilelang` served via the
  package / cpython-compat path: it links libpython and runs the upstream
  TileLang/TVM runtime. **Not provided by this module.**
- `pcc-tilelang-source-subset` — what `import_tilelang_source` /
  `import_tilelang_file` provide: a compiler-side `ast` parse of a DSL subset
  into Kernel IR, with **no execution, no runtime `import tilelang`, and no
  pcc-native `import tilelang` claim.**

The module enforces this with `TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM` /
`TILELANG_SOURCE_SUBSET_CLAIM`, a `tilelang_source_import_claim()` helper,
claim metadata stamped on every returned `KernelModule`
(`tilelang_source_import_claim_of`), and
`assert_not_native_import_tilelang_claim(...)` which rejects any prose that
describes this path as a native `import tilelang`. The authoritative text is
`docs/design/pcc-tilelang-claim-boundary.md`; the gate is
`tests/kernel/test_tilelang_import_claim_boundaries.py`.
