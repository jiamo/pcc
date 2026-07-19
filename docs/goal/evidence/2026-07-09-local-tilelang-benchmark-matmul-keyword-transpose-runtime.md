# Local TileLang benchmark matmul keyword-transpose runtime

Date: 2026-07-09

Scope: move the GPU / TVM-TIRx route closer to the real local TileLang matmul
benchmark source under `~/tilelang/benchmark/matmul/benchmark_matmul.py`.

The local benchmark uses keyword GEMM metadata:

```text
T.gemm(
    A_shared,
    B_shared,
    C_local,
    transpose_B=True,
    policy=policy,
)
```

This slice proves that pcc's strict TileLang importer accepts that source-level
shape directly, not only the older positional `False, True` approximation. The
covered runtime source still uses the existing benchmark-like f16 output-staged
GEMM path with enabled swizzle and `from_warp_partition(2, 1)` policy metadata.

Changes:

- The benchmark-like f16 output-staged TileLang test sources now call
  `T.gemm(..., transpose_B=True, policy=...)` with keyword transpose metadata,
  matching the local benchmark style.
- Added a local-reference regression that reads
  `~/tilelang/benchmark/matmul/benchmark_matmul.py`, imports `matmul/main`
  with static constants, freezes it through Kernel IR/plain TIR, checks
  `swizzle` / `tir.use_swizzle`, checks `policy == (2, 1)`, runs the CPU oracle,
  and emits scalar Metal source.
- The local-reference regression is skipped only if `~/tilelang` is absent; on
  this machine it ran and passed.

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
  tests/kernel/test_tilelang_import_broader.py::test_local_tilelang_matmul_benchmark_source_imports_freezes_and_emits_scalar_metal \
  tests/kernel/test_tilelang_import_broader.py::test_output_staged_f16_transpose_b_gemm_policy_metadata_fails_closed \
  tests/kernel/test_tilelang_import_broader.py::test_output_staged_f16_transpose_b_gemm_warp_partition_call_fails_closed -rs
# 4 passed

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
# 56 passed

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
# 83 passed
```

Remaining boundary:

- Direct local benchmark import is proven for a small static `M=5,N=7,K=3`
  configuration, not arbitrary benchmark dimensions or autotuner execution.
- `from_warp_partition` remains metadata-only for the scalar path; no
  simdgroup/tensorcore policy scheduling or performance claim.
- No arbitrary TileLang calls, policy lists, dynamic constructs, TMA/wgmma,
  external framework interop, or whole-program GPU execution claim.
