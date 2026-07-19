# Local TileLang sparse GEMM_SP boundary

Date: 2026-07-09

Scope: broaden TileLang/TIRx importer and Kernel IR dtype robustness against
the real local `~/tilelang/benchmark/matmul/benchmark_matmul_sp.py` sparse GEMM
source without claiming sparse GEMM execution.

What changed:

- Kernel IR now represents narrow integer POD dtypes needed by sparse metadata:
  `i8`, `u8`, `i16`, and `u16`.
- TileLang dtype import maps `int8`, `uint8`, `int16`, and `uint16` into those
  Kernel IR dtypes.
- Metal launch/runtime ABI tables now know the byte width and C scalar names
  for the same narrow integer dtypes, so shaped metadata buffers such as
  `E: int16` can be sized at the host/device boundary.
- The local sparse benchmark can now parse through `A_sparse`, `E`, `E_shared`,
  and the sparse metadata shapes. It fails closed at the actual unsupported
  operation: `T.gemm_sp(...)`.
- `T.gemm_sp`, `T.wgmma_gemm_sp`, and `T.tcgen05_gemm_sp` now have a specific
  diagnostic explaining that pcc needs an explicit A_sparse/E metadata decode
  contract, CPU oracle, and Metal lowering before claiming support.

Gates passed:

- `gtimeout 60s env -u LC_ALL uv run python -m py_compile pcc/kernel_ir/ir.py pcc/kernel_ir/tilelang_import.py pcc/kernel_ir/metal_finalize.py pcc/kernel_ir/metal_launch.py pcc/kernel_ir/metal_source_runtime.py pcc/kernel_ir/metal_runtime_abi.py pcc/kernel_ir/metal_invoke.py pcc/kernel_ir/hmm_fence.py pcc/kernel_ir/metal_dlpack.py tests/kernel/test_kernel_ir.py tests/kernel/test_tirx_metal_launch_plan.py tests/kernel/test_tilelang_import_broader.py`
- `gtimeout 90s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_kernel_ir.py::test_narrow_integer_metadata_buffers_validate_and_dump tests/kernel/test_tirx_metal_launch_plan.py::test_launch_plan_computes_narrow_integer_buffer_nbytes -rs`
  -> 2 passed
- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py::test_local_tilelang_sparse_matmul_benchmark_reaches_gemm_sp_boundary -rs`
  -> 1 passed
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs`
  -> 61 passed
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_kernel_ir.py tests/kernel/test_tirx_metal_launch_plan.py tests/kernel/test_hmm_fence.py tests/kernel/test_metal_invoke.py -rs`
  -> 48 passed
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py -rs`
  -> 27 passed

Claim scope:

This is a Kernel IR dtype foundation plus source-import boundary slice. It does
not implement the sparse GEMM math, A_sparse/E decompression, CPU oracle sparse
numeric comparison, Metal sparse source emission, `.metallib` execution, pcc1
launch, five-GC lifetime parity, CUDA sparse MMA/WGMMA parity, performance, or
whole-program GPU execution.
