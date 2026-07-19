# Local TileLang matmul non-roller metallib + pcc1/five-GC proof

Date: 2026-07-09

Scope:

- Directly reads the local reference source
  `~/tilelang/benchmark/matmul/benchmark_matmul.py`.
- Covers one static non-roller configuration:
  `M=5, N=7, K=3, block_M=8, block_N=8, block_K=8,
  thread_num=128, num_stages=1, enable_rasteration=False,
  policy=GemmWarpPolicy.Square, with_roller=False`.
- The imported TileLang prim_func is logically named `main`. pcc now legalizes
  the actual Metal device entry to `pcc_main_kernel` in the descriptor, emitted
  MSL source, launch plan, and Objective-C bridge lookup. The logical Kernel
  IR/TIR function name remains `main`; this is a Metal artifact/launch boundary
  mapping, not a source-test rename.

Implementation notes:

- Added a shared Metal device-entry mapping helper so MSL never emits
  `kernel void main(...)`, which Apple's Metal compiler rejects.
- `plan_metal_launch(...)` now accepts either the logical entry (`main`) or the
  legalized device entry and records the real Metal lookup entry in the launch
  plan.
- Added source/launch-plan regressions for the logical `main` case.

Gates passed:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py pcc/kernel_ir/metal_launch.py \
  tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_metal_launch_plan.py \
  tests/kernel/test_tilelang_import_broader.py tests/kernel/test_metal_metallib_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_local_tilelang_matmul_benchmark_nonroller_config_imports_freezes_and_emits_scalar_metal -rs
```

`1 passed in 0.08s`.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_local_tilelang_matmul_nonroller_metallib_matrix -rs
```

`1 passed in 0.24s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py::test_metallib_runtime_package_executes_local_tilelang_matmul_nonroller_or_records_toolchain_skip -rs
```

Initially failed because Metal rejected `kernel void main(...)`; after the
entry-mapping fix, `1 passed in 2.02s`.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_finalize.py::test_metal_entry_name_legalizes_logical_main \
  tests/kernel/test_tirx_metal_launch_plan.py::test_launch_plan_uses_legal_metal_entry_for_logical_main -rs
```

`2 passed in 0.24s`.

```bash
gtimeout 360s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_PCC1_LAUNCH=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_local_tilelang_matmul_nonroller_metallib -rs
```

`1 passed in 2.06s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_local_tilelang_matmul_nonroller_metallib_lifetime_real_or_skipped -rs
```

`1 passed in 2.12s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_metal_launch_plan.py \
  tests/kernel/test_metal_package.py tests/kernel/test_metal_invoke.py -rs
```

`48 passed in 0.78s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py -rs
```

`59 passed in 0.11s`.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py -rs
```

`9 passed in 7.60s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py tests/kernel/test_tilelang_import_broader.py -rs
```

`86 passed in 0.16s`.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
```

`28 passed in 18.08s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_metal_finalize.py -rs
```

`12 passed in 0.10s`.

Claim boundary:

This proves one direct local TileLang `benchmark_matmul.py` non-roller scalar
fallback configuration through Kernel IR/plain TIR, CPU oracle, Metal source,
offline `.metal -> .air -> .metallib`, host command-buffer launch, pcc1
no-libpython launch, and five-GC lifetime execution under `PCC_GC_BACKEND=0..4`.
It also proves that logical TileLang kernels named `main` are mapped to a legal
Metal device entry at artifact/launch time.

This is not autotuner execution, not a roller-policy implementation, not
simdgroup/tensorcore scheduling for this source path, not arbitrary TileLang
lowering, not external framework interop, not performance proof, and not
whole-program GPU execution.
