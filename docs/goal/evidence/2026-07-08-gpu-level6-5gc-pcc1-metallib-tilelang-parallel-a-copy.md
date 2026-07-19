# 2026-07-08 GPU Level-6 pcc1 Metallib TileLang Parallel A Copy

Track rows:

- `GPU-P0-METALLIB-OFFLINE-CHAIN`
- `GPU-P0-TILELANG-GEMM-RUNTIME-ORACLE`
- `GPU-P1-BROADER-TILELANG-TIRX-PASSES`
- `GPU-P0-METAL-PCC1-LAUNCH-REAL`
- `GPU-P0-METAL-5GC-LIFETIME-REAL`

Claim:

This slice proves one additional imported TileLang/TIRx scalar GEMM workload on
the prebuilt `.metallib` path under a pcc1 no-libpython executable and all five
PCC GC backends. It does not claim whole-program GPU execution.

Workload:

- Source shape: `matmul_parallel_copy`
- Constants: `M=5`, `N=7`, `K=16`, `block_M=8`, `block_N=8`, `block_K=8`
- Schedule shape: legal A tile-copy staging with `T.Parallel(...)`; B shared
  copy stays ordinary serial K-loop copy and C uses ordinary guarded writeback
- Import checks:
  - A copy keeps `parallel_vars=["i", "kk"]`
  - B copy keeps `serial_extent=2` but has no `parallel_vars`
  - C copy keeps empty attrs
- Metal source checks:
  - `for (uint ko = 0u; ko < 2u; ++ko)`
  - `threadgroup half A_shared[64];`
  - `threadgroup half B_shared[64];`
  - `for (uint load = tid; load < 64u; load += 32u)`
  - `threadgroup_barrier(mem_flags::mem_threadgroup);`
  - `C[(row * 7u) + col] = (float)acc;`

Runtime proof:

The strict gate builds one pcc1 no-libpython probe, then runs that same probe
under `PCC_GC_BACKEND=0..4`. Each backend verifies the backend marker inside
the pcc runtime process, allocates real native MTLBuffers, writes f16 A/B
payloads, calls `pcc_metal_metallib_runtime_call_prebuilt(...)` with the
produced `.metallib`, waits for fence completion, reads C back as exact f32
bits, compares against `execute_scalar_tiled_gemm_reference(...)`, and releases
native buffers only after readback/fence-completed launch.

Gates run:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile tests/gpu_hardware/test_metal_pcc1_launch_real.py tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_parallel_a_copy_metallib_matrix tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_parallel_a_copy_metallib_lifetime_real_or_skipped -rs
```

Result: `2 passed in 0.66s`.

```bash
gtimeout 900s env -u LC_ALL PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 PCC_RUN_GPU_5GC_LIFETIME=1 uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_parallel_a_copy_metallib_lifetime_real_or_skipped -rs
```

Result: `1 passed in 3.40s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `87 passed in 0.25s`.

Bootstrap:

No pcc1->pcc2->pcc3 bootstrap was run for this slice. The existing
`build/bootstrap-gpu-level5-pcc1-shim/pcc1` was used by the strict gate and the
resulting probe was checked no-libpython by the test helper.

Still open:

Arbitrary executable `T.Parallel` loop bodies, arbitrary nested/multi-argument
loop forms, arbitrary layout functions/swizzle placement, arbitrary split-K
expressions or non-f32 atomics, TMA/wgmma lowering, broader TileLang/TIRx
variants, external framework DLPack/stream interop, deployment packaging UX,
performance, and whole-program GPU execution.
