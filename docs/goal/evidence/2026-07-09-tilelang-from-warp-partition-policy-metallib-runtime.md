# TileLang from_warp_partition policy metallib runtime

Date: 2026-07-09

Scope: advance the GPU / TVM-TIRx route toward the local TileLang matmul
benchmark roller shape by importing the static policy call:

```text
T.GemmWarpPolicy.from_warp_partition(block_rows, block_cols)
```

The covered benchmark-like GEMM slice is still the output-staged f16
transpose_B workload:

```text
A: f16[M,K]
B: f16[N,K] with transpose_B=True
C_local: f32[block_M,block_N]
C_shared: f16[block_M,block_N]
C: f16[M,N]
T.use_swizzle(panel_size=10, enable=enable_rasteration)
T.gemm(..., policy=T.GemmWarpPolicy.from_warp_partition(2, 1))
```

This is not a performance policy implementation. For the current scalar Metal
path, the policy remains metadata-only. pcc preserves the static warp
partition as `(2, 1)` so future simdgroup/tensorcore lowering can see the
roller hint instead of losing it after enum classification.

Changes:

- `import_tilelang_source(...)` now evaluates exactly
  `T.GemmWarpPolicy.from_warp_partition(<positive int>, <positive int>)` in
  TileLang metadata expressions. Other calls still fail closed.
- The f16 output-staged benchmark-like test source now uses `block_rows` and
  `block_cols` defaults plus the source-level `from_warp_partition(...)` call
  instead of passing a pre-normalized policy string through constants.
- Added fail-closed coverage for invalid warp-partition arguments.
- The same source shape is used by runtime-source, host `.metallib`, pcc1
  no-libpython, and five-GC Level-6 gates.

Verified gates:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_metallib_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py
# success

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_output_staged_f16_transpose_b_gemm_casts_accumulator_to_half_output \
  tests/kernel/test_tilelang_import_broader.py::test_output_staged_f16_transpose_b_gemm_policy_metadata_fails_closed \
  tests/kernel/test_tilelang_import_broader.py::test_output_staged_f16_transpose_b_gemm_warp_partition_call_fails_closed -rs
# 3 passed

gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_output_staged_f16_transpose_b_runtime_source_matches_cpu_oracle -rs
# 1 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py::test_metallib_runtime_package_executes_imported_tilelang_output_staged_f16_transpose_b_gemm_or_records_toolchain_skip -rs
# 1 passed

gtimeout 900s env -u LC_ALL uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on pcc/__main__.py -o build/bootstrap-gpu-level5-pcc1-shim/pcc1
# success

gtimeout 360s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_output_staged_f16_transpose_b_gemm_metallib -rs
# 1 passed

gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_output_staged_f16_transpose_b_gemm_metallib_lifetime_real_or_skipped -rs
# 1 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py -rs
# 55 passed

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
# 27 passed

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py -rs
# 6 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_metal_finalize.py -rs
# 11 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py tests/kernel/test_tilelang_import_broader.py -rs
# 82 passed
```

Remaining boundary:

- `from_warp_partition` is metadata-only for the scalar path; no simdgroup or
  tensorcore policy scheduling claim.
- Only positive static integer arguments are supported.
- No arbitrary TileLang calls, dynamic policy objects, policy lists, or
  autotuner execution are imported.
- No arbitrary or cluster-aware swizzle placement, TMA/wgmma lowering,
  external framework interop, performance claim, or whole-program GPU claim.
