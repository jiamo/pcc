# 2026-07-06 Metal Runtime-Source Package Manifest Evidence

## Summary

This slice strengthens the runtime-source Metal host/device boundary without
claiming `.air/.metallib` production or whole-program GPU execution.

`MetalSourceRuntimePackageResult` can now be persisted as a deterministic JSON
manifest. The manifest records SHA-256 and byte-size checks for produced Metal
source, native MTLBuffer runtime bridge, and runtime-source bridge artifacts.
Verification rejects claim drift, including tampered `runtime_launch_executed`,
`runtime_source_compiled`, and `whole_program_gpu` fields.

The runtime-source executed status is accepted only when the manifest proves the
full Level-4 shape: non-injected bridge invocation, runtime-source compilation,
fence completion, and CPU-oracle match. ABI-only injected calls remain
`runtime_launch_executed=false`.

## Files

- `pcc/kernel_ir/metal_source_runtime.py`
- `pcc/kernel_ir/__init__.py`
- `tests/kernel/test_metal_source_runtime.py`

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_source_runtime.py \
  pcc/kernel_ir/__init__.py \
  tests/kernel/test_metal_source_runtime.py
```

Result: passed.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py::test_runtime_source_package_api_validates_fake_abi_without_execution_claim
```

Result: `1 passed in 0.30s`.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py
```

Result: `7 passed in 0.25s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `3 passed in 2.39s`.

## Claim Boundary

This proves a manifest/verification discipline for runtime-source Metal package
results and keeps the existing runtime-source command-buffer evidence
mode-labeled.

This does not prove `.air/.metallib` production, metallib-backed command-buffer
submission, pcc1-native GPU launch, five-GC GPU lifetime parity, external
framework DLPack/stream interop, performance, or whole-program GPU execution.
