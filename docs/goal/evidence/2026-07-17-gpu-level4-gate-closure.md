# GPU Level-4 Hardware Gate Closure

Date: 2026-07-17

Task: `GPU-P0-HARDWARE-LEVEL-GATES`

## Closed Claim

The Level-4 gate now has a finite, mode-labeled boundary:

- eight strict real-Metal cases require runtime-source compilation, command
  buffer submission, fence completion, device readback, and CPU-oracle match;
- the cases cover pcc-Metal copy/GEMM/simdgroup slices plus the pinned
  TVM/TileLang provider comparison without confusing provider and owner;
- manifest checks preserve artifact hashes and reject claim drift;
- six fast classifier cases prove that missing launch, compilation, fence, or
  CPU equality cannot become Level 4, and reject `whole_program_gpu=true`;
- Level 4 never implies `.metallib`, pcc1-native Level 5, five-GC Level 6, or
  external framework/DLPack support.

The previous `tests/gpu_hardware` directory-wide gate mixed this Level-4 row
with independently owned Level-5, Level-6, metallib, and DLPack tasks. The row
now names its exact strict gate, avoiding repeated pcc1/five-GC work. Those
claims remain tracked by their dedicated task-board rows.

## Validation

```text
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_claim_levels.py -rs
14 passed in 10.48s
```

