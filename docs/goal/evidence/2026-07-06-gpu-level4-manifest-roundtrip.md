# 2026-07-06 GPU Level-4 Manifest Round Trip Evidence

## Summary

The Metal hardware claim-level gates now persist and verify the runtime-source
package manifest for every proven Level-4 device result.

For copy, imported TileLang/TIRx scalar GEMM, and the opt-in 8x8 simdgroup GEMM,
the test writes `metal_source_runtime_package_manifest.json` after the result is
classified as `GPU_LEVEL_4_DEVICE_RESULT`, verifies artifact hashes, and checks
the manifest still records:

- `status == metal_source_runtime_package_executed`
- `runtime_launch_executed == true`
- `runtime_source_compiled == true`
- bridge invocation status `metal_source_runtime_invoked`
- `fence_completed == true`
- CPU oracle comparison status `metal_cpu_oracle_match`
- `metallib_produced == false`
- `whole_program_gpu == false`

The helper only writes this manifest after the device-result classifier passes;
SKIPPED_WITH_REASON paths remain in-band skip evidence and are not promoted to
artifact manifests.

## Files

- `tests/gpu_hardware/test_metal_claim_levels.py`

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  tests/gpu_hardware/test_metal_claim_levels.py \
  pcc/kernel_ir/metal_source_runtime.py
```

Result: passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `3 passed in 2.42s`.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `10 passed in 2.55s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `3 passed in 2.37s`.

## Claim Boundary

This proves manifest round-trip discipline for the existing runtime-source
Level-4 Metal device-result gates.

This does not prove `.air/.metallib` production, metallib-backed
command-buffer submission, pcc1-native GPU launch, five-GC GPU lifetime parity,
external framework DLPack/stream interop, performance, or whole-program GPU
execution.
