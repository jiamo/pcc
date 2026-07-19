# 2026-07-06 GPU Level-5 pcc1 Launch Gate Evidence

## Summary

The pcc1-native Metal launcher track now has an executable claim gate instead
of only a task-board placeholder. This does not prove Level 5 yet. It prevents
Level-4 host-harness device results from being upgraded to
`GPU_LEVEL_5_PCC1_NATIVE`.

The new classifier and gate require all of the following before Level 5 can be
claimed:

- the Level-4 device-result facts still hold: runtime launch executed,
  runtime-source compilation, fence completion, and CPU-oracle match;
- a pcc1-native process executed the launcher path;
- that process was no-libpython;
- it ran the same launcher path, not a separate toy probe;
- the pcc1 process exited with code 0;
- the result does not claim whole-program GPU execution.

By default the new hardware gate returns a mode-labeled
`SKIPPED_WITH_REASON` verdict when `PCC_RUN_GPU_PCC1_LAUNCH=1` is not set.
With the opt-in enabled, a missing/freshness-invalid pcc1 is a hard failure;
if a fresh pcc1 is present, the gate remains red until a real pcc1-native
Metal launcher entrypoint exists.

## Files

- `pcc/kernel_ir/gpu_claims.py`
- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `docs/goal/task-board.yaml`

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/gpu_claims.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py
```

Result: passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `4 passed in 0.17s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `10 passed in 4.56s`.

## Claim Boundary

This proves Level-5 claim hygiene and installs the real gate file named by the
task board. It does not prove that a pcc1-built no-libpython binary has run the
Metal launcher path. `GPU_LEVEL_5_PCC1_NATIVE` remains open until the same
runtime-source or metallib launcher path executes from pcc1 and produces a
Level-4 device result under that pcc1 process.

This also does not prove `.air/.metallib` production, five-GC lifetime parity,
performance, or whole-program GPU execution.
