# 2026-07-06 GPU Level-6 5GC Lifetime Gate Evidence

## Summary

The five-GC Metal lifetime track now has an executable claim gate instead of
only a task-board placeholder. This does not prove Level 6 yet. It prevents
host-harness or environment-label-only Metal runs from being reported as
`GPU_LEVEL_6_5GC_PARITY`.

The new classifier and gate require all of the following before Level 6 can be
claimed:

- the same `workload_id` is used for all five records;
- every backend `0..4` is present exactly once;
- each record's `pcc_gc_backend_env` matches the backend being claimed;
- each backend record independently satisfies the pcc1-native Level-5 proof;
- the Metal launch facts still hold: runtime launch, runtime-source
  compilation, fence completion, and CPU-oracle match;
- native resource release did not happen before fence completion;
- native resource release did happen after fence completion;
- the result does not claim whole-program GPU execution.

The gate explicitly rejects `env_label_only` records. Running the host Python
Metal harness five times with `PCC_GC_BACKEND=0..4` is not enough.

## Files

- `pcc/kernel_ir/gpu_claims.py`
- `tests/gpu_hardware/test_metal_5gc_lifetime_real.py`
- `docs/goal/task-board.yaml`

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/gpu_claims.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py
```

Result: passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `6 passed in 0.05s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `10 passed in 0.10s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_gpu_external_resource_gc0.py \
  tests/python/gc/test_gpu_external_resource_gc1.py \
  tests/python/gc/test_gpu_external_resource_gc2.py \
  tests/python/gc/test_gpu_external_resource_gc3.py \
  tests/python/gc/test_gpu_external_resource_gc4.py
```

Result: `5 passed in 0.25s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/gpu_hardware -rs
```

Result: `16 passed in 4.38s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware -rs
```

Result: `16 passed in 4.18s`.

## Claim Boundary

This proves Level-6 claim hygiene and installs the real gate file named by the
task board. It does not prove that real Metal buffer/fence lifetime has passed
under pcc runtime GC backends `0..4`.

`GPU_LEVEL_6_5GC_PARITY` remains open until the same pcc1-native no-libpython
Metal workload proves device output and fence-deferred native release under
all five production GC backends.

This also does not prove `.air/.metallib` production, performance, or
whole-program GPU execution.
