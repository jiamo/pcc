# 2026-07-08 Metal Metallib Runtime Copy Evidence

## Summary

`GPU-P0-METALLIB-OFFLINE-CHAIN` is no longer blocked by the local Xcode
MetalToolchain. The component is installed through Apple's managed component
path, `xcrun --sdk macosx` can find both `metal` and `metallib`, and pcc now has
a first-class metallib-backed runtime package API:

```python
run_metal_metallib_runtime_package(...)
```

The covered shape is a small Kernel IR f32 copy kernel with static 2x2
`src`/`dst` buffers and one `u32` scalar. The API produces `.metal`, `.air`, and
`.metallib` artifacts, builds/loads the Objective-C `newLibraryWithURL` bridge,
allocates real native `id<MTLBuffer>` buffers, writes host matrix bytes, invokes
the bridge, waits for fence completion, reads `dst` back, compares it to the CPU
oracle, and releases buffers after completion.

The successful result reports:

- `status="metal_metallib_runtime_package_executed"`
- `metallib_produced=True`
- `runtime_launch_executed=True`
- `runtime_source_compiled=False`
- `whole_program_gpu=False`
- `cpu_comparison.status="metal_cpu_oracle_match"`
- `max_abs_error=0.0`

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  pcc/kernel_ir/metal_metallib_runtime.py \
  pcc/kernel_ir/__init__.py \
  tests/kernel/test_metal_metallib_runtime.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_metallib_runtime.py
```

Result: `1 passed in 2.16s`.

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_finalize.py \
  tests/kernel/test_metal_package.py \
  tests/kernel/test_metal_metallib_runtime.py
```

Result: `29 passed in 1.71s`.

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/python/test_gpu_metal.py \
  tests/kernel/test_metal_finalize.py \
  tests/kernel/test_metal_package.py \
  tests/kernel/test_metal_metallib_runtime.py
```

Result: `37 passed in 5.37s`.

## Claim Boundary

This proves the offline `.metal -> .air -> .metallib` artifact chain plus a
metallib-backed command-buffer launch for one shaped f32 copy Kernel IR subset.
It is not runtime-source Metal, not whole-program GPU, not imported TileLang
GEMM metallib-backed execution, not pcc1-native Level-5 proof, not five-GC
Level-6 proof, not DLPack/framework interop, and not performance evidence.
