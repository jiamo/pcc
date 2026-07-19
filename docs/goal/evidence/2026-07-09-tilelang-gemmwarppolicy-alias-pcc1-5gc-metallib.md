# TileLang GemmWarpPolicy alias pcc1 and five-GC metallib proof

Date: 2026-07-09

Scope: promote the `GemmWarpPolicy.FullRow` alias slice from host-only
runtime/metallib proof to pcc1-native no-libpython launch and five-GC lifetime
proof.

This continues the host evidence in
`docs/goal/evidence/2026-07-09-tilelang-gemmwarppolicy-alias-runtime-metallib.md`.
The source shape remains:

```text
from tilelang.tileop.base import GemmWarpPolicy
...
T.gemm(..., transpose_B=True, policy=GemmWarpPolicy.FullRow)
```

Claim:

- The source-subset importer accepts the direct `GemmWarpPolicy.*` alias as
  metadata.
- The f16 output-staged transpose_B scalar GEMM produces an offline Metal
  package: `.metal -> .air -> .metallib`.
- A pcc1-produced no-libpython executable calls
  `pcc_metal_metallib_runtime_call_prebuilt(...)`, launches the produced
  `.metallib`, waits for fence completion, reads f16 output, and matches the
  CPU oracle.
- The same pcc1 workload passes under `PCC_GC_BACKEND=0..4` with native buffer
  release after the fence.

Changes:

- `tests/gpu_hardware/test_metal_pcc1_launch_real.py` adds a
  `GemmWarpPolicy.FullRow` source variant, artifact builder, and Level-5
  pcc1-native metallib launcher test.
- `tests/gpu_hardware/test_metal_5gc_lifetime_real.py` adds the matching
  five-GC lifetime probe and classifier coverage.

Verified gates:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
# success

gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_gemmwarp_policy_alias_metallib_matrix -rs
# 1 passed

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_gemmwarp_policy_alias_metallib -rs
# initially skipped before pcc1 rebuild: no fresh current pcc1 binary

gtimeout 900s env -u LC_ALL uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on pcc/__main__.py -o build/bootstrap-gpu-level5-pcc1-shim/pcc1
# success

gtimeout 360s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_PCC1_LAUNCH=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_gemmwarp_policy_alias_metallib -rs
# 1 passed

gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_gemmwarp_policy_alias_metallib_lifetime_real_or_skipped -rs
# 1 passed
```

Remaining boundary:

- `GemmWarpPolicy.*` is still metadata for the scalar fallback; no warp-policy
  scheduling, simdgroup/tensorcore policy selection, autotuner execution, or
  performance claim.
- This is one static `M=5,N=7,K=3` f16 output-staged transpose_B GEMM slice,
  not arbitrary TileLang policy objects, policy lists, dynamic constructs,
  external framework interop, or whole-program GPU execution.
