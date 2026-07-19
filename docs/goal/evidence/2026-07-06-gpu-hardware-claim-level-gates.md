# GPU Hardware Claim-Level Gates

Date: 2026-07-06

Task: `GPU-P0-HARDWARE-LEVEL-GATES`

## What Changed

- Added `pcc/kernel_ir/gpu_claims.py` as the repo-owned GPU claim classifier.
- Added `tests/gpu_hardware/test_metal_claim_levels.py` as the dedicated
  hardware gate for current Metal runtime-source primitives.
- Exported the claim helpers from `pcc.kernel_ir`.

## Claim Levels Proven

The new hardware gate classifies runtime-source package results into the
`GPU_LEVEL_*` ladder from `docs/design/pcc-gpu-next-work.md`.

On this machine, strict hardware mode proved `GPU_LEVEL_4_DEVICE_RESULT` for:

- `copy`: command buffer submitted, fence completed, device output read back,
  CPU oracle matched.
- `tilelang_scalar_gemm`: imported TileLang/TIRx scalar GEMM submitted through
  the runtime-source path, fence completed, CPU oracle matched.
- `simdgroup_gemm_8x8`: opt-in 8x8 f16/f16->f32 simdgroup GEMM submitted
  through the runtime-source path, fence completed, CPU oracle matched.

Each result also asserts:

- `whole_program_gpu == false`
- `metallib_produced == false`
- `pcc1_native_executed == false`
- `gc_backend_parity == []`

So the gate proves runtime-source `GPU_LEVEL_4_DEVICE_RESULT` only. It does not
claim `.metallib`, pcc1-native launch, or five-GC parity.

## Validation

```text
env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/gpu_claims.py \
  tests/gpu_hardware/test_metal_claim_levels.py
passed

env -u LC_ALL uv run pytest -q -n0 tests/gpu_hardware/test_metal_claim_levels.py -rs
3 passed in 2.41s

PCC_GPU_HARDWARE_STRICT=1 env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
3 passed in 2.21s

env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py
13 passed in 2.24s
```

## Remaining Boundary

This is still `DONE_WEAK`, not `DONE_STRONG`.

Open work:

- `.air/.metallib` offline artifact production remains blocked by the local
  missing Xcode Metal Toolchain component.
- No pcc1 no-libpython GPU launcher proof yet.
- No `PCC_GC_BACKEND=0..4` GPU lifetime parity yet.
- No external framework DLPack capsule/stream interop yet.
- No whole-program GPU claim.
