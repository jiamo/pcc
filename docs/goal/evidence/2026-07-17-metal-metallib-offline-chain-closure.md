# GPU-P0-METALLIB-OFFLINE-CHAIN closure evidence

Claim boundary: pcc owns an offline Metal source-to-AIR-to-metallib artifact
chain and executes the resulting metallib through a real Metal command buffer.
This does not claim arbitrary TileLang programs, framework interoperability,
deployment packaging, or performance.

Focused finalize/package/runtime gate:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/python/test_gpu_metal.py tests/kernel/test_metal_finalize.py tests/kernel/test_metal_package.py tests/kernel/test_metal_metallib_runtime.py
49 passed in 12.18s
```

The gate covers Metal source finalization, offline `.metal -> .air -> .metallib`
production, package loading, command submission/fence completion, device
readback, and CPU-oracle comparison. The current strict pcc1 DLPack device-copy
gate independently consumed the same offline metallib path in a no-libpython
binary (1 passed in 1.94s); its GC0..4 matrix passed in 8.84s.

No full bootstrap, full GCC suite, or redundant hardware gate was run for this
closure.
