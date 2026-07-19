# TileLang output-staged f16 transpose_B metallib runtime

Date: 2026-07-09

Scope: advance the GPU / TVM-TIRx route toward real TileLang matmul semantics
by covering the first benchmark-like output dtype conversion:

```text
A: f16[M,K]
B: f16[N,K] with transpose_B=True
C_local: f32[block_M,block_N]
C_shared: f16[block_M,block_N]
C: f16[M,N]
T.copy(C_local, C_shared)
T.copy(C_shared, C[...])
```

This is not a whole-program GPU claim. It proves a narrower Kernel IR / TIRx
subset with explicit host/device boundaries, Metal source, offline `.metallib`,
pcc1 no-libpython launch, and five-GC lifetime parity.

Changes:

- CPU oracle now allows copy-output GEMM with `C_local` as an f32 accumulator
  and `C_shared/C` as f16, and quantizes copy output to the global C dtype.
- Metal scalar GEMM source now permits f16 copy output and still rejects
  non-f32 split-K atomic output.
- Added fractional f16-roundtripped inputs so the tests exercise the output
  half conversion instead of only integer dot products.
- Added host runtime-source, host `.metallib`, pcc1 no-libpython `.metallib`,
  and five-GC Level-6 coverage for this slice.

Verified gates:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/cpu_reference.py pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_metallib_runtime.py

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_output_staged_f16_transpose_b_gemm_casts_accumulator_to_half_output
# 1 passed

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
# 53 passed

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
# 27 passed

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py -rs
# 6 passed
```

Remaining boundary:

- No arbitrary dtype-converting output-staging contract beyond this f32
  accumulator to f16 output copy path.
- No non-f32 split-K atomic output.
- No arbitrary TileLang scheduling, cluster swizzle, TMA/wgmma, broader
  tensorcore lowering, external framework interop, performance claim, or
  whole-program GPU execution claim.
