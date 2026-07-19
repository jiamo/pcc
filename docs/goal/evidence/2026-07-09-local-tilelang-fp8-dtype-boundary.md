# Local TileLang fp8 dtype boundary

Date: 2026-07-09

Scope: broaden TileLang/TIRx importer coverage against real local TileLang
benchmark source without claiming fp8 execution.

What changed:

- The TileLang source-subset importer can now evaluate static metadata shaped
  like `torch.version.hip is not None` inside conditional expressions when the
  caller supplies the dotted metadata value in `constants`.
- The local `~/tilelang/benchmark/matmul_fp8/benchmark_matmul.py` source is now
  parsed far enough to reach the true unsupported dtype boundary:
  `T.float8_e4m3fn` on this non-HIP path.
- The boundary remains fail-closed. Kernel IR does not claim an fp8 scalar
  type, Metal source is not emitted, `.metallib` is not produced, and no pcc1 or
  five-GC execution claim is made for this fp8 benchmark.

Gates passed:

- `gtimeout 60s env -u LC_ALL uv run python -m py_compile pcc/kernel_ir/tilelang_import.py tests/kernel/test_tilelang_import_broader.py`
- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py::test_local_tilelang_fp8_matmul_benchmark_reaches_dtype_boundary_without_runtime_import -rs`
  -> 1 passed
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs`
  -> 60 passed
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py -rs`
  -> 27 passed

Claim scope:

This is a source-import robustness and claim-boundary slice only. It proves the
importer can interpret the benchmark's static platform dtype branch and reject
fp8 precisely. It does not implement fp8 Kernel IR, fp8 CPU oracle arithmetic,
Metal fp8 source emission, `.metallib` execution, pcc1 launch, five-GC lifetime
parity, autotuner/Roller execution, performance, external framework interop, or
whole-program GPU execution.
