# Local TileLang sparse GEMM_SP CPU oracle

Date: 2026-07-09

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Scope:

- Reads the local TileLang sparse benchmark source:
  `~/tilelang/benchmark/matmul/benchmark_matmul_sp.py`.
- Imports the static `matmul_sp/main` shape through the source-subset importer.
- Represents `T.gemm_sp(A_shared, E_shared, B_shared, C_local, ...)` as Kernel
  IR `gemm_sp`.
- Freezes it to plain TIR as `tir.gemm_sp_expand`.
- Executes a deterministic CPU oracle for the supported 2:4 sparse slice:
  f16 A/B payloads, int16 metadata, `e_factor=16`, no transposes, copy-back C.
- Keeps Metal source lowering fail-closed with a specific sparse GEMM_SP
  diagnostic.

Gates passed:

- `gtimeout 60s env -u LC_ALL uv run python -m py_compile pcc/kernel_ir/ir.py pcc/kernel_ir/tirx_adapter.py pcc/kernel_ir/tilelang_import.py pcc/kernel_ir/cpu_reference.py pcc/kernel_ir/metal_finalize.py pcc/kernel_ir/__init__.py tests/kernel/test_tilelang_import_broader.py`
- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py::test_local_tilelang_sparse_matmul_benchmark_imports_tirx_cpu_oracle_and_metal_fail_closed -rs`
  -> 1 passed
- `gtimeout 240s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs`
  -> 61 passed
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py -rs`
  -> 27 passed
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_metal_finalize.py -rs`
  -> 12 passed

Claim boundaries:

- This is sparse GEMM_SP import, TIRx freeze, and CPU-oracle proof only.
- This is not sparse Metal source emission, not `.metallib` production, not a
  pcc1 launch, not five-GC GPU lifetime proof, not CUDA sparse MMA/WGMMA parity,
  not performance proof, and not whole-program GPU execution.
