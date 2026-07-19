# Evidence: TVM/TIRx Metal Host/Device Split

task: `GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT`

status: `DONE_WEAK`

## Changed Files

- `pcc/kernel_ir/metal_finalize.py`
- `pcc/kernel_ir/host_device_split.py`
- `pcc/kernel_ir/__init__.py`
- `pcc/kernel_ir/ir.py`
- `pcc/kernel_ir/tirx_adapter.py`
- `pcc/kernel_ir/tvm_oracle.py`
- `pcc/kernel_ir/tilelang_import.py`
- `pcc/kernel_ir/cpu_reference.py`
- `pcc/kernel_ir/metal_buffer.py`
- `pcc/kernel_ir/metal_invoke.py`
- `pcc/kernel_ir/metal_source_runtime.py`
- `pcc/kernel_ir/metal_tensor.py`
- `pcc/kernel_ir/metal_dlpack.py`
- `pcc/kernel_ir/metal_verify.py`
- `pcc/kernel_ir/metal_package.py`
- `pcc/kernel_ir/metal_launch.py`
- `pcc/gpu_metal.py`
- `tests/kernel/test_tirx_metal_host_device_split.py`
- `tests/kernel/test_tilelang_import.py`
- `tests/kernel/test_tilelang_import_broader.py`
- `tests/kernel/test_kernel_cpu_reference.py`
- `tests/kernel/test_metal_buffer.py`
- `tests/kernel/test_metal_invoke.py`
- `tests/kernel/test_metal_source_runtime.py`
- `tests/kernel/test_metal_tilelang_gemm_runtime.py`
- `tests/kernel/test_metal_simdgroup_gemm.py`
- `tests/kernel/test_metal_tensor.py`
- `tests/kernel/test_metal_dlpack_ownership.py`
- `tests/kernel/test_metal_verify.py`
- `tests/kernel/test_metal_package.py`
- `tests/kernel/test_tirx_metal_launch_plan.py`
- `tests/kernel/test_kernel_ir.py`
- `tests/kernel/test_tirx_adapter.py`
- `tests/kernel/test_tvm_oracle.py`
- `tests/benchmarks/tile/harness.py`
- `tests/benchmarks/tile/test_tile_bench.py`
- `docs/design/pcc-kernel-ir.md`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`

## Claim

The TVM/TIRx/Metal route now has a concrete host/device split proof for the
first global-buffer/threadgroup kernel subset:

- `emit_metal_source(...)` emits an inspectable, compilable `.metal` kernel
  source artifact from validated Kernel IR after the TIRx/plain-TIR freeze.
- `finalize_metal(..., artifact_dir=...)` writes the `.metal` artifact, and
  `compile_toolchain=True` wires the existing Metal CLI path for `.air` and
  `.metallib` production.
- `build_host_launch_boundaries(...)` records a CPU-owned host launch boundary:
  host backend, device target, launcher symbol, and POD/buffer argument
  bindings.
- Device-local storage is now modeled as `LocalBuffer`, not as a host-visible
  `BufferParam`. The TIRx freeze preserves locals, the TVM oracle projects them
  as `alloc_buffers`, the host-launch boundary reports them under
  `device_locals`, and the Metal source emitter declares supported threadgroup
  locals in the kernel body.
- A bounded threadgroup `sum` reduction now emits inspectable Metal source for
  `global src -> threadgroup accumulator -> global out`, including
  `threadgroup_position_in_grid` output indexing and explicit
  `threadgroup_barrier` synchronization.
- Thread-private fragment/local copy/fill staging now emits inspectable Metal
  source using explicit `thread` storage while still staying outside the CPU
  host launch argument list.
- `import_tilelang_source(...)` imports the first strict TileLang Metal matmul
  Python-DSL subset into pcc `KernelModule` without executing TileLang/TVM:
  `T.Tensor` parameters preserve tensor shapes, `T.Kernel` preserves grid and
  thread count, `T.alloc_shared`/`T.alloc_fragment` become device-local
  `LocalBuffer`s, and `T.clear`/`T.copy`/`T.gemm`/`T.Pipelined` become pcc
  kernel ops/attrs. Unknown TileLang constructs fail closed.
- The importer now has the first broader schedule-metadata slice beyond the
  original 2-D matmul shape: split-k-shaped 3-D `T.Kernel(...)` grids survive
  import and TIRx freeze, and positional
  `T.gemm(A, B, C, transpose_A, transpose_B)` operands are preserved as attrs
  rather than silently dropped. Conflicting positional/keyword transpose
  metadata fails closed.
- The importer also accepts one-extent `T.serial(...)` loops around the current
  supported copy/gemm body subset. It preserves `serial_extent` metadata through
  Kernel IR and TIRx freeze; the CPU oracle and Metal source emitter validate
  that the serial extent matches the computed K-tile extent before executing or
  emitting source.
- The importer now preserves `T.Parallel(...)` loop extent/name metadata for
  the supported nested op forms. CPU oracle and Metal source lowering accept
  the legal A/B global-to-shared tile-copy staging form only when the parallel
  extents match the destination tile shape; mismatched or non-staging
  `T.Parallel` metadata still fails closed. A runtime-source Metal package
  probe for that accepted form submits a command buffer and matches CPU output.
- The imported TileLang Metal matmul shape now emits inspectable scalar tiled
  Metal GEMM source. The source uses explicit threadgroup A/B tiles, uniform
  barrier placement across all threads, static shape/bounds guards for
  edge tiles, and one scalar `float acc` per output element. This is an honest
  slow lowering path; it does not claim simdgroup, tensorcore, or performance
  semantics.
- `execute_scalar_tiled_gemm_reference(...)` now executes the same frozen
  plain-TIR scalar tiled GEMM subset on CPU data as a numeric oracle. It
  validates the same shape/scope/grid/pipeline boundaries, handles edge tiles
  and K-tail tiles, and returns outputs with `runtime_launch_executed=False`.
  Unsupported variants such as `transpose_A`, `transpose_B`, bad input shapes,
  or mismatched pipeline metadata fail closed. This proves the current imported
  GEMM subset has a CPU correctness baseline before any GPU runtime claim.
- `plan_metal_launch(...)` validates a Kernel IR Metal launch packet against
  the host boundary and HMM/fence model: buffer handles must be live, on a Metal
  device, dtype-compatible, and large enough for static tensor shapes; scalars
  must be POD via `PccPackedArgs.validate()`. The launch plan records
  `dispatchThreadgroups`, threads-per-threadgroup, command-encoder steps, and
  the fact that a fence is required on commit. `prepare_metal_launch(...,
  execute=True)` still returns `SKIPPED_WITH_REASON` because no Kernel IR
  command-buffer executor exists yet.
- `emit_metal_executor_bridge_source(...)` lowers that validated launch plan to
  Objective-C bridge code. The bridge source maps the plan to the
  real Metal API surface (`MTLCreateSystemDefaultDevice`, `newLibraryWithURL`,
  `newFunctionWithName`, `newComputePipelineStateWithFunction`, command queue,
  command buffer, compute encoder, `setBuffer`, `setBytes`,
  `dispatchThreadgroups`, `commit`, and a command-buffer completion handler
  that calls the pcc fence hook).
- `build_metal_executor_bridge_artifacts(...)` now writes that `.m` bridge
  artifact and optionally compiles it to a host Objective-C `.o` artifact via
  the existing Metal runtime bridge compile helper. Missing bridge toolchains
  report `SKIPPED_WITH_REASON` after source emission, while compiler rejection
  of generated bridge source is a `MetalLaunchError` rather than a hidden skip.
  The object artifact is a host-side launch bridge only; it is not a `.metallib`
  and does not execute a command buffer.
- `build_metal_kernel_package(...)` now ties the proof surfaces into one
  non-executing manifest: optional CPU reference result, Metal finalize/source
  artifact result, validated launch plan, and host executor bridge artifact. If
  CPU inputs are supplied, the CPU oracle must pass before any artifacts are
  written. The package manifest keeps `runtime_launch_executed=False` and
  `whole_program_gpu=False`.
- `write_metal_kernel_package_manifest(...)` now writes a deterministic JSON
  package manifest with SHA-256 and byte-size records for every produced
  artifact (`.metal`, bridge `.m`, and bridge `.o` when produced).
  `verify_metal_kernel_package_manifest(...)` reloads the manifest and fails if
  any recorded artifact is missing, resized, rehashed, or if launch-claim fields
  drift to runtime execution / whole-program GPU.
- `link_metal_runtime_bridge_dylib(...)` and the package-level
  `link_bridge_library=True` path now optionally link the host Objective-C
  bridge object into a loadable `.dylib` artifact. The dylib is recorded in the
  package manifest as `bridge.library` when produced. This is still only a host
  bridge artifact; it is not loaded or invoked by pcc runtime in this slice.
- `validate_dynamic_library_symbol(...)` and package-level
  `validate_bridge_library=True` now validate the produced bridge dylib by
  performing host `dlopen`/`dlsym` for the generated bridge symbol. The symbol
  is resolved from the same `MetalLaunchPlan` used by bridge source emission.
  Validation does not call the bridge function and does not submit work to
  Metal.
- `build_metal_native_buffer_binding_set(...)` now records a separate native
  buffer-binding proof for runtime-supplied `id<MTLBuffer>` pointers. It keys
  the mapping by logical `PccBufferHandle.handle_id`, requires exactly the
  launch plan's buffer handle set, rejects missing/extra handles and zero
  pointers, and keeps `runtime_launch_executed=False`. This keeps the logical
  pcc handle namespace separate from native Metal object pointers.
- `emit_metal_native_buffer_runtime_source(...)` emits a small Objective-C C
  ABI runtime bridge for native `id<MTLBuffer>` ownership:
  create/length/release plus host byte write/read only.
  `build_metal_native_buffer_runtime_artifacts(...)`
  writes that source, can compile a host object, link a host dylib, and validate
  the create/length/release/write/read symbols.
  `smoke_metal_native_buffer_runtime(...)` creates one native MTLBuffer, checks
  its length, and releases it through the bridge.
  `allocate_metal_native_buffers_for_plan(...)` allocates native MTLBuffers for
  every launch-plan buffer argument, validates reported size, produces a
  `MetalNativeBufferBindingSet`, and owns release through `release_all()`.
  `write_metal_native_buffer(...)`, `read_metal_native_buffer(...)`, and
  `roundtrip_metal_native_buffer_bytes(...)` prove host bytes can be copied into
  and back out of a native shared MTLBuffer without a GPU kernel. This runtime
  bridge still does not create a command queue, command buffer, encoder,
  dispatch, or fence completion.
- `build_metal_bridge_invocation_packet(...)` and package-level
  `build_invocation_packet=True` now record the generated bridge function's C
  ABI packet shape after dylib load/symbol validation: sidecar metallib path,
  buffer-handle pointer slots, scalar pointer slots, fence callback requirement,
  `wait_until_completed`, and the resolved bridge symbol. The packet is
  deliberately marked non-invocable when either the metallib is missing or the
  native buffer binding set is absent. With a matching native binding set, the
  packet marks buffer slots as native-bound but still stays non-invocable on
  this machine because there is no produced metallib artifact. Strict mode
  rejects a missing metallib instead of pretending launch readiness.
- `invoke_metal_bridge_packet(...)` is the first strict host bridge invocation
  wrapper. It refuses non-invocable packets, requires a real metallib path,
  native buffer slots, a validated bridge dylib, and a `PccFenceToken` when the
  bridge expects completion. It also refuses unmanaged async callback lifetime
  unless the packet was built with `wait_until_completed=True`. Injected CDLL
  calls are recorded as ABI validation only and never as GPU execution. A real
  bridge call returning non-zero is recorded as bridge-called / launch-failed,
  not as command-buffer execution.
- `pack_matrix_to_metal_bytes(...)`, `write_metal_launch_matrices(...)`, and
  `read_metal_launch_matrix(...)` now connect CPU-oracle-shaped row-major
  matrices to native MTLBuffers by launch-plan argument name. They use the
  launch plan's static dtype/shape/nbytes metadata, reject wrong matrix shapes
  or released allocation sets, can zero-fill unprovided shaped buffers for
  output initialization, and keep `runtime_launch_executed=False`. This is the
  typed data plane needed for future CPU oracle vs Metal output comparison.
- `verify_metal_launch_output_against_cpu_reference(...)` now reads a native
  output buffer through the launch-plan matrix metadata and compares it against
  `CpuReferenceResult.outputs[...]` with explicit `atol`/`rtol`. It rejects
  ambiguous output names, shape mismatch, and element mismatch with coordinates.
  The verifier can now carry an explicit completed-launch claim from its caller
  instead of hard-coding a no-launch readback result.
- `pcc/kernel_ir/metal_source_runtime.py` adds a separate runtime-source launch
  path that does **not** pretend to produce or consume a `.metallib`. It emits
  an Objective-C bridge with `newLibraryWithSource`, validates native
  `id<MTLBuffer>` bindings, ABI-packs scalar pointers and a pcc fence callback,
  refuses unmanaged async callback lifetime, and distinguishes injected-CDLL ABI
  validation from real command-buffer execution.
- `run_metal_source_runtime_package(...)` promotes that bridge into a
  first-class package/runtime API. It builds the non-executing
  `MetalKernelPackage`, builds/loads the native MTLBuffer runtime, builds/loads
  the runtime-source bridge, allocates launch-plan native buffers, writes host
  matrices, invokes the runtime-source bridge, compares completed-launch
  readback against the CPU oracle, releases native allocations, and returns one
  claim-scoped result. The existing package manifest remains non-executing;
  only `MetalSourceRuntimePackageResult` can report runtime-source execution.
- A real local runtime-source probe built/loaded the native-buffer runtime
  dylib and runtime-source bridge dylib, allocated shaped f32 `src`/`dst`
  native MTLBuffers, wrote `src`, zero-filled `dst`, compiled a Metal copy
  kernel through `newLibraryWithSource`, submitted a command buffer, completed
  the pcc fence, read back `dst`, and matched the CPU oracle with
  `max_abs_error=0.0`. The newer package-level probe performs the same flow
  through `run_metal_source_runtime_package(...)` and returns
  `metal_source_runtime_package_executed`, `runtime_launch_executed=True`,
  `runtime_source_compiled=True`, `allocations_released=True`, and
  `whole_program_gpu=False`. This proves a runtime-source package/command-buffer
  boundary for the copy subset. It does not prove offline `.air/.metallib`
  production, metallib-backed launch, TileLang/GEMM runtime output,
  external framework DLPack capsule interop, simdgroup/tensorcore lowering, or
  whole-program GPU execution.
- The imported TileLang/TIRx scalar GEMM source now also runs through the same
  runtime-source package path for a small shaped f16/f16->f32 matmul. The first
  attempt failed honestly at `newLibraryWithSource` with a Metal diagnostic
  showing mixed scalar/vector thread-position attributes. The emitter now uses
  `uint2 tid2 [[thread_position_in_threadgroup]]` with `uint2 tgid
  [[threadgroup_position_in_grid]]` and derives the linear thread id from
  `tid2.x`. The real local GEMM probe then compiled through
  `newLibraryWithSource`, submitted a command buffer, completed the fence, read
  back `C`, and matched the CPU oracle with `max_abs_error=0.0` for shape
  `(5, 7)`.
- `emit_metal_simdgroup_gemm_source(...)` is an explicit opt-in path for the
  first 8x8x8 f16/f16->f32 Metal simdgroup GEMM microkernel. It keeps the
  default scalar tiled GEMM path unchanged, accepts only 8x8 local tiles with
  exactly 32 threads, emits `simdgroup_half8x8`, `simdgroup_float8x8`,
  `make_filled_simdgroup_matrix`, `simdgroup_load`,
  `simdgroup_multiply_accumulate`, and `simdgroup_store`, and rejects larger,
  edge-tile, or non-simdgroup-legal shapes instead of silently falling back.
- A real local 8x8 simdgroup GEMM runtime-source package probe compiled the
  opt-in source through `newLibraryWithSource`, submitted a command buffer,
  completed the fence, read back f32 `C`, and matched the CPU oracle with
  `max_abs_error=0.0` for f16/f16 inputs. This proves only the 8x8 microkernel
  runtime-source path; it is not `.air/.metallib` production, not a
  metallib-backed launch, and not a whole-program GPU claim.
- The metadata/package proof surfaces keep `whole_program_gpu=False` and
  `ordinary_python_runs_on_host=True`. Only the explicit runtime-source
  invocation/package result reports `runtime_launch_executed=True`, currently
  for the shaped copy-kernel proof, the small imported TileLang/TIRx scalar
  GEMM proof, and the first 8x8 simdgroup GEMM microkernel proof.
- `metal_dlpack.py` now models the pcc-owned DLPack-shaped tensor ownership
  layer over native MTLBuffer allocations: one-shot consume/import, POD
  `PccBufferHandle` re-entry, alias-counted deleters, per-handle native release,
  and release deferral behind a `PccFenceToken` until command-buffer completion
  is observed.

This proves `Metal source artifact + host launch boundary` for the first
global-buffer copy/fill-shaped subset plus threadgroup-local copy/fill staging,
and proves the first TileLang Python-DSL Metal matmul shape can enter pcc Kernel
IR, freeze to plain TIR, project to the TVM object shape, and expose only global
params at the host launch boundary, execute the current scalar tiled GEMM
semantics against a CPU oracle, then emit a scalar tiled Metal GEMM source
artifact, a validated launch plan, a host Objective-C executor bridge
source/object/library artifact pipeline, validate that the dylib exports the
expected bridge symbol, record the non-executing bridge invocation ABI packet
shape including distinct native-buffer binding readiness, and a single
non-executing package manifest that records all of those surfaces together with
artifact hashes. The native-buffer runtime slice also proves local
Objective-C dylib build/load plus actual native MTLBuffer allocation, length
inspection, launch-plan binding, host byte write/read roundtrip, and release
without submitting GPU work. It
also proves the strict host bridge invocation wrapper can ABI-pack a ready
packet and can call the real host bridge up to a failing metallib load boundary
without claiming GPU execution. The native matrix marshalling slice additionally
proves row-major f32 matrices can be copied into launch-plan native buffers,
zero-filled for outputs, and read back by name with dtype/shape validation. The
native readback verifier proves that output buffers can be compared against the
CPU oracle with tolerance and coordinate-level mismatch diagnostics. The
runtime-source bridge slice proves one real command-buffer submit/fence/readback
path for a shaped f32 copy kernel using `newLibraryWithSource`, and the imported
TileLang/TIRx scalar GEMM slice proves device-computed C output against the CPU
oracle for a small shaped f16/f16->f32 matmul. The DLPack-shaped ownership
slice proves pcc-managed tensor import/export ownership and fence-safe native
release for launch-plan MTLBuffer allocations. The opt-in simdgroup slice proves
one 8x8 f16/f16->f32 Metal simdgroup microkernel through runtime-source
command-buffer execution and CPU-oracle readback. It does not prove
whole-program GPU execution.

## Reference Source

- LLVM full depth-1 reference: `~/pcc_refs/llvm-project-20.1.8-full-depth1`
  at `87f0227cb60147a26a1eeb4fb06e3b505e9c7261`
- Apache TVM full depth-1 reference: `~/pcc_refs/apache-tvm-full-depth1`
  at `cfb98e938c8d9525648c75fbebcb8944edb952fe`
- Local TileLang reference: `~/tilelang` (`/Users/jiamo/tilelang`)
  at `ed00dfcd7f9c200e1150896b1be59c41ff3e8d9d`
- Local TileLang vendored TVM reference: `/Users/jiamo/tilelang/3rdparty/tvm`
  at `1ecfcc2e1e1fb9f75db9ed760a97aa9687372905`

The LLVM infrastructure article lessons are internalized rather than kept as a
download-path dependency. The applied lesson is LLVM's modular architecture:
keep IR, target capability, address spaces/data layout, pass boundaries,
artifact production, host launch, and runtime execution as separate claims.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tirx_metal_host_device_split.py`
  - result: `15 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py`
  - result: `9 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py`
  - result: `9 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_metal_finalize.py`
  - result: `42 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_kernel_cpu_reference.py`
  - result: `4 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_package.py`
  - result: `21 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_buffer.py`
  - result: `8 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_invoke.py`
  - result: `3 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tensor.py`
  - result: `4 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_dlpack_ownership.py`
  - result: `4 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_hmm_fence.py tests/kernel/test_metal_tensor.py tests/kernel/test_metal_buffer.py tests/kernel/test_metal_dlpack_ownership.py`
  - result: `28 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_verify.py`
  - result: `4 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_source_runtime.py`
  - result: `7 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py`
  - result: `2 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py`
  - result: `4 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_metal_finalize.py tests/kernel/test_tilelang_import.py tests/kernel/test_metal_tilelang_gemm_runtime.py tests/kernel/test_metal_simdgroup_gemm.py`
  - result: `26 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tirx_metal_launch_plan.py`
  - result: `15 passed`
- local TileLang reference probe:
  - command: `import_tilelang_file("/Users/jiamo/tilelang/benchmark/matmul_metal/benchmark_matmul_metal.py", outer_function="matmul_simdgroup", prim_func="gemm_kernel", constants={"M":128,"N":256,"K":64})`
  - result: module `tilelang_metal_matmul_probe`, function `gemm_kernel`,
    grid `(4, 2)`, threads `128`, tensor shapes `[(128, 64), (64, 256), (128, 256)]`,
    local shapes `[(64, 32), (32, 64), (64, 64)]`, ops
    `['fill', 'copy', 'copy', 'gemm', 'copy']`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py`
  - result: `20 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_kernel_ir.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py`
  - result: `34 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_metal_host_device_split.py tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_kernel_ir.py`
  - result: `59 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tirx_metal_launch_plan.py tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_metal_host_device_split.py tests/kernel/test_hmm_fence.py tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_kernel_ir.py`
  - result: `86 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_tirx_metal_launch_plan.py tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_metal_host_device_split.py tests/kernel/test_hmm_fence.py tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_kernel_ir.py`
  - result: `90 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_package.py tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_tirx_metal_launch_plan.py tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_metal_host_device_split.py tests/kernel/test_hmm_fence.py tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_kernel_ir.py`
  - result: `111 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_buffer.py tests/kernel/test_metal_package.py`
  - result: `27 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_invoke.py tests/kernel/test_metal_buffer.py tests/kernel/test_metal_package.py`
  - result: `32 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tensor.py tests/kernel/test_metal_buffer.py tests/kernel/test_metal_invoke.py tests/kernel/test_metal_package.py`
  - result: `36 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_verify.py tests/kernel/test_metal_tensor.py tests/kernel/test_metal_buffer.py tests/kernel/test_metal_invoke.py tests/kernel/test_metal_package.py`
  - result: `39 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_verify.py tests/kernel/test_metal_tensor.py tests/kernel/test_metal_invoke.py tests/kernel/test_metal_buffer.py tests/kernel/test_metal_package.py tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_tirx_metal_launch_plan.py tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_metal_host_device_split.py tests/kernel/test_hmm_fence.py tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_kernel_ir.py`
  - result: `129 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_source_runtime.py tests/kernel/test_metal_verify.py tests/kernel/test_metal_tensor.py tests/kernel/test_metal_invoke.py tests/kernel/test_metal_buffer.py tests/kernel/test_metal_package.py tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_tirx_metal_launch_plan.py tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_metal_host_device_split.py tests/kernel/test_hmm_fence.py tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_kernel_ir.py`
  - result: `137 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py tests/kernel/test_metal_source_runtime.py tests/kernel/test_metal_verify.py tests/kernel/test_metal_tensor.py tests/kernel/test_metal_invoke.py tests/kernel/test_metal_buffer.py tests/kernel/test_metal_package.py tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_tirx_metal_launch_plan.py tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_metal_host_device_split.py tests/kernel/test_hmm_fence.py tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_kernel_ir.py`
  - result: `138 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_kernel_ir.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_tirx_metal_host_device_split.py`
  - result: `42 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/benchmarks/tile`
  - result: `49 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_target_split.py tests/kernel/test_metal_finalize.py tests/benchmarks/tile`
  - result: `65 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel`
  - result: `187 passed`
- real runtime-source Metal 8x8 simdgroup GEMM package probe:
  - command: emit the opt-in simdgroup GEMM source for an 8x8x8 f16/f16->f32
    Kernel IR module, compute the CPU reference output, then run
    `run_metal_source_runtime_package(...)` with f16 A/B buffers and f32 C
    output
  - result: `metal_source_runtime_package_executed`,
    `metal_source_runtime_invoked`, return code `0`, `fence_completed=True`,
    `runtime_source_compiled=True`, `runtime_launch_executed=True`,
    `metal_cpu_oracle_match`, `max_abs_error=0.0`,
    `allocations_released=True`, `whole_program_gpu=False`; no `.air` or
    `.metallib` was produced or claimed
- real imported TileLang/TIRx GEMM runtime-source package probe:
  - command: import the strict TileLang matmul source with `M=5,N=7,K=3`,
    compute `execute_scalar_tiled_gemm_reference(...)`, emit scalar Metal GEMM
    source, then `run_metal_source_runtime_package(...)` with f16 A/B buffers
    and f32 C output
  - result: first diagnostic run returned rc=4 with Metal compile error
    explaining mixed scalar/vector thread-position inputs; after changing GEMM
    source to use `uint2 tid2 [[thread_position_in_threadgroup]]`, the real run
    returned `metal_source_runtime_package_executed`,
    `metal_source_runtime_invoked`, return code `0`, `fence_completed=True`,
    `runtime_source_compiled=True`, `runtime_launch_executed=True`,
    shape `(5, 7)`, `metal_cpu_oracle_match`, `max_abs_error=0.0`,
    `allocations_released=True`, `whole_program_gpu=False`; no `.air` or
    `.metallib` was produced or claimed
- real runtime-source Metal package API probe:
  - command: `run_metal_source_runtime_package(...)` for a shaped f32
    `copy_kernel`, with package/source artifacts, native-buffer runtime
    build/load, runtime-source bridge build/load, `src`/`dst` native buffer
    allocation, matrix write, command-buffer invoke, CPU-oracle readback
    comparison, and release
  - result: `metal_source_runtime_package_executed`,
    `metal_source_runtime_bridge_load_validated`,
    `metal_native_buffer_runtime_load_validated`,
    invocation `metal_source_runtime_invoked`, return code `0`,
    `fence_completed=True`, `runtime_source_compiled=True`,
    `runtime_launch_executed=True`, readback
    `((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))`,
    `metal_cpu_oracle_match`, `max_abs_error=0.0`,
    `allocations_released=True`, `whole_program_gpu=False`; no `.air` or
    `.metallib` was produced or claimed
- real runtime-source Metal command-buffer probe:
  - command: build/load the native-buffer runtime dylib and runtime-source
    bridge dylib, allocate shaped f32 `src`/`dst` buffers, write `src`,
    zero-fill `dst`, invoke `newLibraryWithSource` bridge for a Metal copy
    kernel, wait for completion, read back `dst`, and compare against
    `CpuReferenceResult(outputs={"dst": src})`
  - result: `metal_source_runtime_bridge_load_validated`,
    `metal_source_runtime_invoked`, return code `0`, `fence_completed=True`,
    `runtime_source_compiled=True`, `runtime_launch_executed=True`,
    readback `((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))`,
    `metal_cpu_oracle_match`, `max_abs_error=0.0`, native buffers released;
    no `.air/.metallib` was produced or claimed
- real native MTLBuffer runtime allocation probe:
  - command: `build_metal_native_buffer_runtime_artifacts("/tmp/pcc_native_buffer_allocation_probe", compile_runtime=True, link_runtime_library=True, validate_symbols=True, timeout=60.0)` plus `allocate_metal_native_buffers_for_plan(...)` against a two-buffer launch plan
  - result: `metal_native_buffer_runtime_load_validated`,
    `metal_native_buffer_allocations_ready`, 2 native buffers allocated,
    non-zero native pointers, reported sizes `[64, 64]`,
    `runtime_launch_executed=False`, released successfully; no command buffer
    committed
- real native MTLBuffer host byte roundtrip probe:
  - command: `build_metal_native_buffer_runtime_artifacts("/tmp/pcc_native_buffer_roundtrip_probe", compile_runtime=True, link_runtime_library=True, validate_symbols=True, timeout=60.0)`, `allocate_metal_native_buffers_for_plan(...)`, then `roundtrip_metal_native_buffer_bytes(..., b"pcc-metal-roundtrip", offset=8)`
  - result: validated runtime symbols
    `pcc_metal_buffer_runtime_create`, `pcc_metal_buffer_runtime_length`,
    `pcc_metal_buffer_runtime_release`, `pcc_metal_buffer_runtime_write`,
    `pcc_metal_buffer_runtime_read`; roundtrip status
    `metal_native_buffer_data_roundtrip_validated`, readback
    `pcc-metal-roundtrip`, `runtime_launch_executed=False`, released
    successfully; no command buffer committed
- real native matrix marshalling/readback probe:
  - command: build/load the native buffer runtime dylib, allocate native buffers
    for a launch plan with shaped f32 buffers `A` and `C`, run
    `write_metal_launch_matrices(..., {"A": matrix}, zero_fill_unprovided=True)`,
    then `read_metal_launch_matrix(...)` for both `A` and `C`
  - result: `metal_matrix_buffers_ready` for `A` and zero-filled `C`,
    `metal_matrix_readback_validated` for both buffers, `A` read back as
    `((1.0, 2.5, -3.0), (4.0, 5.5, 6.25))`, `C` read back as all zeros,
    `runtime_launch_executed=False`, released successfully; no command buffer
    committed
- real native readback vs CPU oracle comparison probe:
  - command: build/load the native buffer runtime dylib, allocate shaped f32
    buffers, write simulated output matrix `C` into the native buffer, construct
    `CpuReferenceResult(outputs={"C": expected})`, then
    `verify_metal_launch_output_against_cpu_reference(..., output_name="C")`
  - result: `metal_cpu_oracle_match`, shape `(2, 2)`, `element_count=4`,
    `max_abs_error=0.0`, `runtime_launch_executed=False`, released
    successfully; no command buffer committed
- real host Objective-C bridge invocation boundary probe:
  - command: build/load a real host bridge dylib, build/load the native buffer
    runtime dylib, allocate two native MTLBuffers, write a placeholder metallib
    file, build a strict ready packet with `wait_until_completed=True`, then
    call `invoke_metal_bridge_packet(...)`
  - result: packet `metal_bridge_invocation_packet_ready`, bridge function
    called, return code `4` from `newLibraryWithURL` on the placeholder
    metallib, `runtime_launch_executed=False`, fence not completed, native
    buffers released
- real host Objective-C bridge dylib probe:
  - command: `build_metal_kernel_package(_module(), _packed_args(), "/tmp/pcc_bridge_dylib_probe", compile_bridge=True, link_bridge_library=True, timeout=30.0)` plus manifest write/verify
  - result: `metal_executor_bridge_object_produced`,
    `metal_bridge_library_produced`,
    `/tmp/pcc_bridge_dylib_probe/gemm_kernel_metal_bridge.dylib`,
    `/tmp/pcc_bridge_dylib_probe/metal_kernel_package_manifest.json`; no dylib
    load, no command buffer committed
- real host Objective-C bridge dylib load/symbol probe:
  - command: `build_metal_kernel_package(_module(), _packed_args(), "/tmp/pcc_bridge_dylib_load_probe", compile_bridge=True, link_bridge_library=True, validate_bridge_library=True, timeout=30.0)` plus manifest write/verify
  - result: `metal_bridge_library_produced`,
    `metal_bridge_library_load_validated`,
    `__pcc_launch_gemm_kernel_metal_runtime_bridge`,
    `/tmp/pcc_bridge_dylib_load_probe/gemm_kernel_metal_bridge.dylib`,
    `/tmp/pcc_bridge_dylib_load_probe/metal_kernel_package_manifest.json`; bridge
    symbol resolved, bridge function not called, no command buffer committed
- real host Objective-C bridge object probe:
  - command: `build_metal_executor_bridge_artifacts(plan_metal_launch(imported_tilelang_matmul, packed_metal_args), "/tmp/pcc_bridge_compile_probe", compile_bridge=True, timeout=30.0)`
  - result: `metal_executor_bridge_object_produced`,
    `/tmp/pcc_bridge_compile_probe/gemm_kernel_metal_bridge.m`,
    `/tmp/pcc_bridge_compile_probe/gemm_kernel_metal_bridge.o`; no command
    buffer committed
- `env -u LC_ALL uv run pytest -q -n0 tests/python/test_gpu_metal.py`
  - result: `2 passed, 5 skipped`
- `env -u LC_ALL uv run python -m py_compile pcc/kernel_ir/ir.py pcc/kernel_ir/tilelang_import.py pcc/kernel_ir/tirx_adapter.py pcc/kernel_ir/tvm_oracle.py pcc/kernel_ir/metal_finalize.py pcc/kernel_ir/metal_launch.py pcc/kernel_ir/metal_buffer.py pcc/kernel_ir/metal_invoke.py pcc/kernel_ir/metal_source_runtime.py pcc/kernel_ir/metal_tensor.py pcc/kernel_ir/metal_dlpack.py pcc/kernel_ir/metal_verify.py pcc/kernel_ir/metal_package.py pcc/kernel_ir/host_device_split.py pcc/kernel_ir/__init__.py pcc/gpu_metal.py tests/kernel/test_tirx_metal_host_device_split.py tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_metal_launch_plan.py tests/kernel/test_metal_buffer.py tests/kernel/test_metal_invoke.py tests/kernel/test_metal_source_runtime.py tests/kernel/test_metal_tilelang_gemm_runtime.py tests/kernel/test_metal_tensor.py tests/kernel/test_metal_dlpack_ownership.py tests/kernel/test_metal_verify.py tests/kernel/test_metal_package.py tests/kernel/test_kernel_ir.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/benchmarks/tile/harness.py tests/benchmarks/tile/test_tile_bench.py`
  - result: passed
- `env -u LC_ALL uv run python scripts/goal_state.py validate`
  - result: `OK: 16 tasks validated`

## Toolchain Boundary

This machine has a path for Xcode's Metal compiler:

`/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/metal`

But the compiler cannot execute:

`error: cannot execute tool 'metal' due to missing Metal Toolchain`

Therefore `.air/.metallib` production is wired and tested as a
`SKIPPED_WITH_REASON` branch on this machine. Do not claim a produced metallib
from this run.

## Open Boundary

- Real `.air/.metallib` production must be rerun on a machine with an executable
  Metal Toolchain component.
- Metallib-backed host-launch execution remains open; this slice proves the
  launcher boundary shape and validated launch packet/command plan, while the
  runtime-source path separately proves command-buffer execution without
  producing or consuming `.metallib`.
- The source emitter intentionally covers only the first global-buffer,
  threadgroup-local, and thread-private fragment/local copy/fill-shaped subset
  plus a bounded threadgroup sum-reduction subset, the imported TileLang matmul
  scalar tiled GEMM subset, and the opt-in 8x8 simdgroup GEMM microkernel.
  Broader simdgroup/tensorcore lowering beyond that microkernel, broader
  TileLang/TIRx pass lowering beyond the strict AST importer subset, and tensor
  runtime integration remain open.
- The TileLang importer is source-AST/static-metadata only. It does not import
  or execute TileLang/TVM, does not run TileLang passes, does not lower
  arbitrary Python control flow, and does not itself execute Metal. The small
  imported GEMM execution proof is the separate runtime-source package gate
  recorded above. The broader-import slice currently proves split-k-shaped
  3-D grid metadata, positional GEMM transpose metadata, one-extent T.serial
  K-loops over the existing copy/gemm subset, and the legal A/B tile-copy
  staging form of T.Parallel through CPU oracle, Metal source, and
  runtime-source device-output comparison. It does not implement real split-k
  atomic accumulation, general executable T.Parallel/T.vectorized loop bodies,
  multi-argument T.serial forms, annotate_layout/use_swizzle, or broader
  TileLang pass execution.
- The CPU reference oracle covers only the current scalar tiled GEMM subset. It
  is a numeric baseline for the imported plain-TIR shape, not a tensor runtime,
  not TileLang execution, not a GPU executor, and not a performance benchmark.
  Transposed GEMM flags are preserved and rejected until their semantics exist.
- The Metal launch planner and executor bridge artifact builder are
  runtime-facing but non-executing. They do not create a Metal device at pcc
  runtime, load a real metallib, commit a command buffer, or complete a fence;
  bridge object production is only a host-side artifact step, and execution
  remains a future Kernel IR runtime slice.
- The package manifest is a proof bundle, not a runtime. It records existing
  proof surfaces together and refuses to proceed past a failing CPU oracle, but
  it does not schedule, submit, or wait for a Metal command buffer. Manifest
  hash verification proves local artifact integrity only; it is not a proof of
  device execution.
- The bridge dylib is a loadable host artifact boundary only. It is not linked
  into pcc binaries here and not called. The load/symbol validation path proves
  only host `dlopen`/`dlsym` for the bridge symbol, not `.metallib` loading or
  GPU dispatch.
- The bridge invocation packet is an ABI-shape proof only. It records where the
  runtime will pass metallib path, buffer handles, scalar pointers, fence
  callback, and wait flag, but it is `invocable=false` until pcc has a produced
  metallib and native `id<MTLBuffer>` bindings for every packed buffer. Native
  bindings are represented by a separate `MetalNativeBufferBindingSet`, not by
  reinterpreting `PccBufferHandle.handle_id` as a pointer.
- The native-buffer runtime bridge can allocate, length-check, bind, and release
  native MTLBuffers for a launch plan and round-trip host bytes through a shared
  native MTLBuffer. The pcc-owned DLPack-shaped ownership layer now covers
  import/export ownership and fence-safe release for those allocations, but the
  native-buffer runtime is still not a pcc runtime scheduler and not
  command-buffer submission.
- The native matrix marshalling path uses static launch-plan shape/dtype
  metadata for row-major host matrices, but it is not a live tensor runtime,
  not an external framework DLPack capsule ABI, and not a proof of
  device-computed output.
- The CPU-oracle comparison path is proven against native readback data.
  Device-computed output is proven for runtime-source copy, small imported
  TileLang scalar GEMM, and 8x8 simdgroup GEMM probes only; metallib-backed
  device-computed output remains open.
- The strict host bridge invocation wrapper has ABI coverage and a real
  failure-boundary probe, but successful command-buffer submission still needs a
  produced valid metallib and end-to-end fence completion proof.
- External tensor framework interop remains open: no real torch/MLX/MPS DLPack
  capsule ABI, stream synchronization, or cross-runtime ownership handoff is
  claimed by the pcc-owned DLPack-shaped state machine.
- No pcc-native MLX/vLLM/TileLang package runtime claim is made.
