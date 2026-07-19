# GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT closure evidence

Finite claim: pcc represents the TVM/TIRx device kernel separately from its CPU
host launch boundary, emits a Metal artifact, and validates the launch-facing
target split. This does not claim arbitrary TVM/TileLang passes, whole-program
GPU execution, framework support, or performance.

Current split and benchmark-harness gate:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tirx_metal_host_device_split.py tests/kernel/test_target_split.py tests/benchmarks/tile
73 passed in 0.85s
```

The same source state passed Metal source-runtime/package verification (35
tests in 1.00s) and the finalize/offline-metallib/runtime suite (49 tests in
12.18s). These gates prove device locals do not leak into host arguments,
target selection is explicit, artifacts are inspectable, and the host boundary
can consume a real Metal package.

No full bootstrap or full GCC suite was run.
