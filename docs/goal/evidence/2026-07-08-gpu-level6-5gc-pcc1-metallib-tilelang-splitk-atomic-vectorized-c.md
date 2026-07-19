# GPU Level-6 pcc1 metallib TileLang split-K atomic T.vectorized C output

Date: 2026-07-08

Scope:
- Added an imported TileLang/TIRx scalar GEMM slice where split-K atomic output
  staging is written as an outer `T.Parallel(block_M)` loop and inner
  `T.vectorized(block_N)` loop.
- This is a prebuilt `.metallib` path, not runtime-source Metal and not a
  whole-program GPU claim.
- The covered shape is `M=5,N=7,K=16,split_k=2,block_M=8,block_N=8,block_K=4`.

Evidence:
- The importer preserves scheduled atomic-output metadata:
  `parallel_vars=["i"]`, `parallel_extents=[8]`, `vectorized_var="j"`, and
  `vectorized_extent=8`.
- Metal finalization still emits an atomic output pointer, z-axis split index,
  zero-start K loop, and `atomic_fetch_add_explicit`.
- The runtime package produces `.metal -> .air -> .metallib`, builds and
  load-validates the host bridge, creates native Metal buffers, launches the
  produced metallib from a pcc1 no-libpython executable through
  `pcc_metal_metallib_runtime_call_prebuilt(...)`, waits for fence completion,
  reads exact f32 output, and compares against the CPU oracle.
- The strict gate runs the same pcc1 executable under `PCC_GC_BACKEND=0..4`,
  releasing native buffers only after the fence-completed readback.

Gates:
- `gtimeout 60s env -u LC_ALL uv run python -m py_compile tests/gpu_hardware/test_metal_pcc1_launch_real.py tests/gpu_hardware/test_metal_5gc_lifetime_real.py`
  - passed
- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_splitk_atomic_vectorized_c_metallib_matrix tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_splitk_atomic_vectorized_c_metallib_lifetime_real_or_skipped -rs`
  - `2 passed in 0.71s`
- `gtimeout 900s env -u LC_ALL PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 PCC_RUN_GPU_5GC_LIFETIME=1 uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_splitk_atomic_vectorized_c_metallib_lifetime_real_or_skipped -rs`
  - `1 passed in 3.17s`

Not claimed:
- No pcc1->pcc2->pcc3 bootstrap proof was run for this slice.
- No arbitrary split-K expression, non-f32 atomic, arbitrary executable
  `T.Parallel`/`T.vectorized` loop body, broader TileLang/TIRx pass coverage,
  performance, external framework interop, or whole-program GPU claim.
