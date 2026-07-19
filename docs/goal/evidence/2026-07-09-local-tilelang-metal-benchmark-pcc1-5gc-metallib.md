# Local TileLang Metal benchmark pcc1 and five-GC metallib proof

Date: 2026-07-09

Scope: turn the local TileLang Metal benchmark source file into a regression
input for pcc's Kernel IR / TIRx / Metal path:

```text
~/tilelang/benchmark/matmul_metal/benchmark_matmul_metal.py
```

This source is the direct Metal benchmark shape with `@tilelang.jit`,
`T.Kernel`, shared A/B tiles, fragment C, `T.Pipelined`, `T.copy`, and
`T.gemm(A_shared, B_shared, C_local)`. The covered configuration is a small
static `M=5,N=7,K=3,block_M=8,block_N=8,block_K=8` scalar fallback slice.

Claim:

- The local benchmark file parses directly through the strict TileLang source
  subset importer.
- The imported function freezes through Kernel IR/plain TIR and matches the
  CPU oracle.
- The same local-file shape emits Metal source, produces `.metal -> .air ->
  .metallib`, launches through the host metallib bridge, and matches the CPU
  oracle.
- A pcc1-produced no-libpython executable launches the prebuilt `.metallib`
  through `pcc_metal_metallib_runtime_call_prebuilt(...)`.
- The same pcc1 workload passes under `PCC_GC_BACKEND=0..4` with native Metal
  buffers released after fence completion.

Changes:

- `tests/kernel/test_tilelang_import_broader.py` adds direct local
  `benchmark_matmul_metal.py` import/freeze/source coverage.
- `tests/kernel/test_metal_metallib_runtime.py` adds direct local
  `benchmark_matmul_metal.py` host metallib execution coverage.
- `tests/gpu_hardware/test_metal_pcc1_launch_real.py` adds local benchmark
  artifact construction and a Level-5 pcc1-native metallib launcher gate.
- `tests/gpu_hardware/test_metal_5gc_lifetime_real.py` adds the matching
  Level-6 classifier and real five-GC lifetime gate.

Verified gates:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_metallib_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
# success

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_local_tilelang_metal_matmul_benchmark_source_imports_freezes_and_emits_scalar_metal -rs
# 1 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py::test_metallib_runtime_package_executes_local_tilelang_metal_benchmark_or_records_toolchain_skip -rs
# 1 passed

gtimeout 180s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_PCC1_LAUNCH=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_local_tilelang_metal_benchmark_metallib -rs
# 1 passed

gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_local_tilelang_metal_benchmark_metallib_lifetime_real_or_skipped -rs
# 1 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py -rs
# 58 passed

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py -rs
# 8 passed

gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_local_tilelang_metal_benchmark_metallib_matrix -rs
# 1 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py tests/kernel/test_tilelang_import_broader.py -rs
# 85 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_metal_finalize.py -rs
# 11 passed
```

Remaining boundary:

- This is one static scalar fallback configuration from the local Metal
  benchmark file, not a performance benchmark result.
- It does not execute the benchmark's PyTorch/MPS comparison path, CLI sweep,
  or large shapes.
- It is not simdgroup/tensorcore scheduling, arbitrary TileLang lowering,
  autotuner execution, external framework interop, or whole-program GPU
  execution.
