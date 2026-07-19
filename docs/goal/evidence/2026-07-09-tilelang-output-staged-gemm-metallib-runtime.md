# TileLang output-staged GEMM runtime/metallib evidence

Date: 2026-07-09

Tasks:

- `GPU-P1-BROADER-TILELANG-TIRX-PASSES`
- `GPU-P0-METALLIB-OFFLINE-CHAIN`
- `GPU-P0-METAL-PCC1-LAUNCH-REAL`
- `GPU-P0-METAL-5GC-LIFETIME-REAL`

Slice: accept the TileLang benchmark-style output path
`C_local -> C_shared -> C[...]` for the current strict scalar tiled GEMM subset.

What changed:

- The CPU oracle and Metal source matcher now recognize a two-copy output path
  where `C_local` is first copied into a shared `C_shared` tile and then copied
  to global `C`.
- This is deliberately fail-closed: `C_shared` must be a local shared buffer,
  rank/shape must match `C_local`, and `C_local`/`C_shared`/global `C` dtypes
  must match. It is not treated as arbitrary executable shared-memory semantics.
- Scheduled tile-copy metadata now accepts both output edges
  `C_local -> C_shared` and `C_shared -> C` when their extents match the C tile.
- The generated scalar GEMM Metal source still writes global `C` directly from
  the accumulator. The proof is semantic equivalence for this no-cast staging
  subset, not a claim that pcc now lowers arbitrary threadgroup C store/load
  programs.
- The same imported TileLang/TIRx shape has host `.metallib` execution proof:
  `.metal -> .air -> .metallib`, bridge load via `newLibraryWithURL`,
  command-buffer submit, fence completion, device readback, and CPU-oracle
  match.
- The same shape has pcc1 no-libpython `.metallib` launcher proof through
  `pcc_metal_metallib_runtime_call_prebuilt(...)`.
- The same pcc1 `.metallib` workload has Level-6 five-GC proof under
  `PCC_GC_BACKEND=0..4`.

Evidence:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_metallib_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_output_staged_gemm_survives_import_freeze_source_and_cpu_oracle -rs
# 1 passed in 0.33s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_output_staged_runtime_source_matches_cpu_oracle -rs
# 1 passed in 1.75s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py::test_metallib_runtime_package_executes_imported_tilelang_output_staged_gemm_or_records_toolchain_skip -rs
# 1 passed in 2.16s

gtimeout 900s env -u LC_ALL uv run pcc --backend self \
  --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o build/bootstrap-gpu-level5-pcc1-shim/pcc1
# build/bootstrap-gpu-level5-pcc1-shim/pcc1.tmp: replacing existing signature

gtimeout 300s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_output_staged_gemm_metallib -rs
# 1 passed in 3.08s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_output_staged_gemm_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_output_staged_gemm_metallib_lifetime_real_or_skipped -rs
# 2 passed in 0.49s

gtimeout 600s env -u LC_ALL \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_output_staged_gemm_metallib_lifetime_real_or_skipped -rs
# 1 passed in 1.89s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py -rs
# 79 passed in 0.14s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
# 26 passed in 15.71s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py -rs
# 5 passed in 3.97s
```

Claim boundary:

- This proves Kernel IR import, plain TIR freeze, CPU oracle, Metal source,
  runtime-source Metal, host `.metallib`, pcc1 no-libpython `.metallib`, and
  Level-6 five-GC execution for the exact no-cast output-staging shape
  `C_local -> C_shared -> C` with `M=5,N=7,K=3,block=8`.
- This does not prove arbitrary shared-memory output semantics, dtype-converting
  output staging, arbitrary executable `T.Parallel`/`T.vectorized` bodies,
  arbitrary layout functions, or whole-program GPU execution.
