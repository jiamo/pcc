# TileLang split-K floor-plus-one ceildiv alias runtime/metallib evidence

Date: 2026-07-09

Tasks:

- `GPU-P1-BROADER-TILELANG-TIRX-PASSES`
- `GPU-P0-METALLIB-OFFLINE-CHAIN`
- `GPU-P0-METAL-PCC1-LAUNCH-REAL`
- `GPU-P0-METAL-5GC-LIFETIME-REAL`

Slice: accept the common positive-integer ceildiv idiom
`splitK = (K - 1) // split_k + 1` as split-K span metadata for the current
strict TileLang scalar GEMM subset.

What changed:

- The TileLang source importer now recognizes the exact `((K - 1) // split_k)
  + 1` and `1 + ((K - 1) // split_k)` shape as equivalent to
  `T.ceildiv(K, split_k)` for split-K copy-span provenance.
- The importer preserves this through the outer static alias path, so
  `splitK = (K - 1) // split_k + 1` can be used inside `T.Pipelined(...)` and
  `T.copy(...)` indices.
- The resulting Kernel IR copy ops carry `split_k_span_mode="ceildiv"` and
  `split_k_span=5` for the non-divisible proof case `K=17, split_k=4`.
- The same imported TileLang/TIRx shape now has host `.metallib` execution
  proof: `.metal -> .air -> .metallib`, bridge load via `newLibraryWithURL`,
  command-buffer submit, fence completion, device readback, and CPU-oracle
  match.
- The same shape now has pcc1 no-libpython `.metallib` launcher proof:
  a pcc1-produced executable calls
  `pcc_metal_metallib_runtime_call_prebuilt(...)`, waits for the fence,
  reads back exact f32 bits, and stays no-libpython.
- The same pcc1 `.metallib` workload now has Level-6 five-GC proof under
  `PCC_GC_BACKEND=0..4`: each backend marker is verified inside the pcc
  runtime process, the same executable launches the produced `.metallib`,
  output matches the CPU oracle, and native buffers are released after the
  completed fence.

Evidence:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_metallib_runtime.py

gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_splitk_atomic_floor_plus_one_ceildiv_alias_survives_import_freeze_source_and_cpu_oracle -rs
# 1 passed in 0.06s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_splitk_atomic_floor_plus_one_ceildiv_alias_runtime_source_matches_cpu_oracle -rs
# 1 passed in 1.47s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py -rs
# 78 passed in 0.31s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
# 25 passed in 16.36s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py::test_metallib_runtime_package_executes_imported_tilelang_splitk_floor_plus_one_ceildiv_alias_or_records_toolchain_skip -rs
# 1 passed in 2.71s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py -rs
# 4 passed in 5.28s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gpu_metal.py \
  tests/kernel/test_metal_finalize.py \
  tests/kernel/test_metal_package.py \
  tests/kernel/test_metal_metallib_runtime.py -rs
# 40 passed in 37.68s

gtimeout 900s env -u LC_ALL uv run pcc --backend self \
  --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o build/bootstrap-gpu-level5-pcc1-shim/pcc1
# build/bootstrap-gpu-level5-pcc1-shim/pcc1.tmp: replacing existing signature

gtimeout 300s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_splitk_floor_plus_one_ceildiv_metallib -rs
# 1 passed in 3.53s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_splitk_floor_plus_one_ceildiv_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_splitk_floor_plus_one_ceildiv_metallib_lifetime_real_or_skipped -rs
# 2 passed in 0.51s

gtimeout 600s env -u LC_ALL \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_splitk_floor_plus_one_ceildiv_metallib_lifetime_real_or_skipped -rs
# 1 passed in 3.46s
```

Claim boundary:

- This proves Kernel IR import, plain TIR freeze, CPU oracle, Metal source, and
  runtime-source Metal command-buffer/device-readback execution for the exact
  floor-plus-one ceildiv alias shape under `K=17, split_k=4`.
- This also proves the host offline Metal artifact chain for the same shape:
  `.metal -> .air -> .metallib`, `newLibraryWithURL`, command-buffer submit,
  fence completion, device readback, and CPU-oracle match.
- This now proves the pcc1 no-libpython prebuilt `.metallib` launcher path for
  this exact alias shape at GPU Level 5.
- This now proves the same exact alias shape at GPU Level 6 under
  `PCC_GC_BACKEND=0..4`.
- This does not prove arbitrary split-K expressions, arbitrary executable
  `T.Parallel`/`T.vectorized` bodies, new atomics, or whole-program GPU
  execution.
