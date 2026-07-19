# 2026-07-06 GPU Level-5 pcc1 Launch Preflight Evidence

## Summary

The Level-5 pcc1-native Metal launcher gate now has a static preflight bound to
the real runtime-source launcher closure. The preflight reads the launcher
source with `ast` and follows in-repo `pcc.*` imports without importing or
executing the launcher modules.

Current result: the launcher path is not pcc1/no-libpython ready. The blockers
are concrete:

- `ctypes_dynamic_ffi`: `metal_source_runtime.py`, `metal_buffer.py`, and
  `pcc/gpu_metal.py` depend on CPython `ctypes`;
- `ctypes_cdll_load`: the current launcher loads Objective-C/Metal bridge
  dylibs through `ctypes.CDLL`;
- `host_subprocess_toolchain`: `pcc/gpu_metal.py` shells out to `xcrun/clang`.

The synchronized `waitUntilCompleted` path no longer registers a Python
`ctypes.CFUNCTYPE` fence callback. The bridge still accepts a nullable callback
slot for future async use, but the default pcc launcher passes NULL and marks
the pcc fence complete only after the synchronous native wait returns success.

The gate still does not claim Level 5. In non-strict opt-in mode a fresh pcc1
plus these blockers returns a mode-labeled `SKIPPED_WITH_REASON`; with
`PCC_REQUIRE_CURRENT_PCC1=1`, the same blocker is a hard failure.

## Files

- `pcc/kernel_ir/pcc1_metal_preflight.py`
- `pcc/kernel_ir/metal_source_runtime.py`
- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `tests/kernel/test_metal_source_runtime.py`
- `docs/goal/task-board.yaml`

## Gates

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_source_runtime.py \
  pcc/kernel_ir/pcc1_metal_preflight.py \
  tests/kernel/test_metal_source_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py -rs
```

Result: `7 passed in 0.11s`.

```bash
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `105 passed in 37.77s`.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `6 passed in 0.53s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/gpu_hardware -rs
```

Result: `18 passed in 4.42s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware -rs
```

Result: `18 passed in 4.37s`.

```bash
gtimeout 60s env -u LC_ALL uv run python scripts/goal_state.py validate
```

Result: `OK: 27 tasks validated`.

```bash
gtimeout 30s git diff --check
```

Result: passed.

## Claim Boundary

This proves only that the current real Metal launcher closure has an executable,
mode-labeled pcc1 readiness preflight. It does not prove a pcc1-built
no-libpython process has executed the launcher path.

`GPU_LEVEL_5_PCC1_NATIVE` remains open until pcc has a no-libpython dynamic
library / FFI boundary that can load the Metal bridge, pack pointer/scalar ABI
arguments, run host toolchain steps without host Python ownership, and run the
same launcher path with a Level-4 device result from the pcc1 process.
