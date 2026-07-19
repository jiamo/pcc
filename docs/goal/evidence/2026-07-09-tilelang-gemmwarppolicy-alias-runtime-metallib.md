# TileLang GemmWarpPolicy alias runtime and metallib coverage

Date: 2026-07-09

Scope: accept the common TileLang source form that imports `GemmWarpPolicy`
directly and passes `policy=GemmWarpPolicy.FullRow` into `T.gemm(...)`.

This is a metadata-entry coverage slice for the existing f16 output-staged
transpose_B scalar GEMM path. It does not claim that the scalar Metal path
implements warp-policy scheduling, simdgroup/tensorcore policy selection, pcc1
execution, five-GC parity, or performance.

Changes:

- `pcc/kernel_ir/tilelang_import.py` now evaluates attribute metadata shaped as
  `GemmWarpPolicy.*` in addition to `T.GemmWarpPolicy.*`.
- `tests/kernel/test_tilelang_import_broader.py` proves the alias survives
  source import, Kernel IR, plain TIR freeze, CPU oracle execution, and Metal
  source emission.
- `tests/kernel/test_metal_tilelang_gemm_runtime.py` proves the alias variant
  runs through runtime-source Metal command-buffer execution and matches the
  CPU oracle.
- `tests/kernel/test_metal_metallib_runtime.py` proves the alias variant
  produces `.metal -> .air -> .metallib`, loads through `newLibraryWithURL`,
  launches, completes the fence, reads back device output, and matches the CPU
  oracle.

Verified gates:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_metallib_runtime.py
# success

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_imported_gemmwarp_policy_alias_survives_import_freeze_source_and_cpu_oracle -rs
# 1 passed

gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_gemmwarp_policy_alias_runtime_source_matches_cpu_oracle -rs
# 1 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py::test_metallib_runtime_package_executes_imported_tilelang_gemmwarp_policy_alias_or_records_toolchain_skip -rs
# 1 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py -rs
# 57 passed

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
# 28 passed

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py -rs
# 7 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py tests/kernel/test_tilelang_import_broader.py -rs
# 84 passed

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_metal_finalize.py -rs
# 11 passed
```

Remaining boundary:

- `GemmWarpPolicy.*` is preserved as metadata for the scalar fallback. It is
  not executed as a warp scheduling policy.
- This slice did not rebuild pcc1 and did not run Level-5 or Level-6 gates, so
  it is only host importer/runtime-source/metallib evidence.
- Arbitrary TileLang policy objects, policy lists, autotuner execution,
  simdgroup/tensorcore scheduling, dynamic constructs, external framework
  interop, whole-program GPU execution, and performance remain open.
