# TileLang policy and enabled-swizzle output-staged f16 metallib runtime

Date: 2026-07-09

Scope: advance the GPU / TVM-TIRx route toward the local TileLang benchmark
shape by carrying scheduling metadata through the existing output-staged f16
transpose_B GEMM slice:

```text
policy = GemmWarpPolicy.Square
enable_rasteration = True
T.use_swizzle(panel_size=10, enable=enable_rasteration)
T.Pipelined(..., num_stages=num_stages)
T.gemm(..., transpose_B=True, policy=policy)
C_local(f32) -> C_shared(f16) -> C(f16)
```

This is not a whole-program GPU claim and not an arbitrary TileLang policy
implementation. The policy is accepted as explicit metadata for the current
scalar Metal path; unsupported policy objects fail closed. Enabled swizzle is
preserved as Kernel IR metadata, lowered to plain TIR as `tir.use_swizzle`, and
emitted in Metal source as the existing row-rasterization tile-id transform.

Changes:

- `import_tilelang_source(...)` now normalizes `T.gemm(policy=...)` into a
  strict metadata form: `GemmWarpPolicy.*` strings or positive two-integer
  warp-partition pairs only.
- CPU oracle and Metal scalar source validation accept the same policy
  metadata but keep it metadata-only for this scalar path.
- The output-staged f16 transpose_B TileLang test source now mirrors the
  benchmark-style outer parameters `num_stages`, `thread_num`, `policy`, and
  `enable_rasteration`.
- The f16 output-staged test now checks both Kernel IR `swizzle` metadata and
  plain TIR `tir.use_swizzle` metadata, plus Metal source row-rasterization
  emission and half-output casting.

Verified gates:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_metallib_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
# success

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_output_staged_f16_transpose_b_gemm_casts_accumulator_to_half_output \
  tests/kernel/test_tilelang_import_broader.py::test_output_staged_f16_transpose_b_gemm_policy_metadata_fails_closed -rs
# 2 passed

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
# 54 passed

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
# 81 passed
```

One intermediate file-level metallib run failed because the new swizzle source
assertion was accidentally placed in the non-swizzled output-staged test. The
assertion was moved to the f16 benchmark-like test and the final file-level
gate passed.

Remaining boundary:

- Policy is metadata-only for the scalar path; no arbitrary TileLang warp
  policy implementation or performance claim.
- Enabled swizzle is proven for the covered row-rasterization metadata shape,
  not arbitrary or cluster-aware swizzle placement.
- No arbitrary dtype-converting output staging beyond this f32 accumulator to
  f16 output copy path.
- No non-f32 split-K atomic output, TMA/wgmma lowering, external framework
  interop, or whole-program GPU execution claim.
