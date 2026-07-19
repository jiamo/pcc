# Local TileLang matmul static roller-config metallib + pcc1/five-GC proof

Date: 2026-07-09

Scope:

- Directly reads the local reference source
  `~/tilelang/benchmark/matmul/benchmark_matmul.py`.
- Covers one static roller-hint-like configuration:
  `M=5, N=7, K=3, block_M=8, block_N=8, block_K=8,
  thread_num=32, num_stages=0, enable_rasteration=True,
  policy=(2, 1), with_roller=True`.
- This does not execute TileLang's autotuner or Roller search. The constants
  are supplied to the benchmark body so pcc can prove the generated kernel
  path, policy metadata, swizzle metadata, Metal artifact, host launch, pcc1
  launch, and five-GC lifetime behavior for this configuration.
- The TileLang prim_func is logically named `main`; the Metal artifact and
  bridge continue to use the legalized device entry `pcc_main_kernel`.

Gates passed:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  tests/kernel/test_metal_metallib_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py::test_metallib_runtime_package_executes_local_tilelang_matmul_static_roller_or_records_toolchain_skip -rs
```

`1 passed in 2.25s`.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_local_tilelang_matmul_static_roller_metallib_matrix -rs
```

`1 passed in 0.65s`.

```bash
gtimeout 360s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_PCC1_LAUNCH=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_local_tilelang_matmul_static_roller_metallib -rs
```

`1 passed in 1.86s` after the runtime archive repair below.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_local_tilelang_matmul_static_roller_metallib_lifetime_real_or_skipped -rs
```

`1 passed in 3.01s` after the runtime archive repair below.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_local_tilelang_matmul_nonroller_metallib_lifetime_real_or_skipped -rs
```

`1 passed in 3.19s`, confirming the prior non-roller Level-6 workload was
restored after the archive repair.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py -rs
```

`10 passed in 8.82s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py -rs
```

`59 passed in 0.11s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py tests/kernel/test_tilelang_import_broader.py -rs
```

`86 passed in 0.19s`.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
```

`28 passed in 19.10s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_finalize.py tests/kernel/test_tirx_metal_launch_plan.py \
  tests/kernel/test_metal_package.py tests/kernel/test_metal_invoke.py -rs
```

`48 passed in 1.02s`.

Runtime archive repair note:

During verification, I accidentally forced a runtime rebuild with
`PYTHON=pcc1`, but this Makefile path invokes `-c`, which the bootstrap pcc1
CLI does not support. That left `libpy_runtime_pcc_py_libpython.a` in a stale
state, and both the new static roller-config Level-6 gate and the previously
green non-roller Level-6 gate failed with undefined `py_*` / `pcc_gc_*`
runtime symbols. I rebuilt the archive with the expected host Python:

```bash
gtimeout 300s env -u LC_ALL make -C pcc/py_runtime \
  libpy_runtime_pcc_py_libpython.a \
  PCC=/Users/jiamo/my/pcc/build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 \
  PCC_IR_TO_OBJ='/Users/jiamo/my/pcc/.venv/bin/python3 -m pcc.tools.ir_to_obj'
```

That rebuild passed, and both Level-6 gates passed afterward.

Claim boundary:

This proves one direct local TileLang `benchmark_matmul.py` static
roller-hint-like scalar fallback configuration through Kernel IR/plain TIR,
CPU oracle, Metal source, offline `.metal -> .air -> .metallib`, host
command-buffer launch, pcc1 no-libpython launch, and five-GC lifetime execution
under `PCC_GC_BACKEND=0..4`.

This is not autotuner execution, not Roller search execution, not
simdgroup/tensorcore scheduling for this source path, not arbitrary TileLang
lowering, not external framework interop, not performance proof, and not
whole-program GPU execution.
