# GPU Next-Work Routing Hardening

Date: 2026-07-06

Task: planning/reference routing for GPU, TVM/TIRx, Metal, GPU-GC,
distributed, and ds4 work.

## Input Read

- `/Users/jiamo/Downloads/codex_pcc_gpu_next_work.md`
- `docs/design/pcc-gpu-next-work.md`
- `docs/goal/task-board.yaml`
- `docs/current-goal-state.md`
- `codex-goal-prompt.md`
- `AGENTS.md`

## Reference Pins Verified

```text
~/pcc_refs/llvm-project-20.1.8-full-depth1
  cfb98e938c8d9525648c75fbebcb8944edb952fe
~/pcc_refs/apache-tvm-full-depth1
  87f0227cb60147a26a1eeb4fb06e3b505e9c7261
~/tilelang
  ed00dfcd7f9c200e1150896b1be59c41ff3e8d9d
~/tilelang/3rdparty/tvm
  1ecfcc2e1e1fb9f75db9ed760a97aa9687372905
~/pcc_refs/antirez-ds4-depth1
  80ebbc396aee40eedc1d829222f3362d10fa4c6c
```

The older `docs/current-goal-state.md` LocalBuffer evidence row had the LLVM
and Apache TVM commit IDs swapped; that row now matches the verified reference
trees and `docs/design/pcc-gpu-next-work.md`.

## What Changed

- Added `docs/design/pcc-gpu-next-work.md` to the AGENTS repository map as the
  durable GPU / TVM-TIRx / Metal / GPU-GC / distributed / ds4 route contract.
- Added `pcc/kernel_ir/`, `pcc/gpu_gc/`, and `pcc/dist/` to the AGENTS
  repository map with their claim boundaries.
- Routed GPU-GC, TVM/TIRx/TileLang, distributed runtime, and ds4 branch work
  through `docs/design/pcc-gpu-next-work.md` in `docs/current-goal-state.md`.
- Added explicit task-board rows for the remaining GPU claim-level boundaries:
  `GPU-P0-METAL-PCC1-LAUNCH-REAL` and
  `GPU-P0-METAL-5GC-LIFETIME-REAL`.
- Added explicit ds4 follow-up rows:
  `DS4-P1-CPU-COMPILE-SUBSET`, `DS4-P2-GPU-API-MAPPING`, and
  `DS4-P3-PRIMITIVE-ORACLE`.
- Updated `docs/design/pcc-gpu-next-work.md` active task mapping so the
  durable design and task board agree.

## Validation

```text
gtimeout 60s env -u LC_ALL uv run python scripts/goal_state.py validate
OK: 27 tasks validated

gtimeout 60s env -u LC_ALL uv run python scripts/goal_state.py next
id: AUD-P0-GC-SLOT-VISITOR
priority: P0
status: DONE_WEAK

gtimeout 20s git diff --check -- AGENTS.md docs/current-goal-state.md \
  docs/goal/task-board.yaml docs/design/pcc-gpu-next-work.md
passed with no output

gtimeout 20s rg -n \
  "GPU-P0-METAL-PCC1-LAUNCH-REAL|GPU-P0-METAL-5GC-LIFETIME-REAL|\
DS4-P1-CPU-COMPILE-SUBSET|DS4-P2-GPU-API-MAPPING|DS4-P3-PRIMITIVE-ORACLE|\
pcc-gpu-next-work" \
  AGENTS.md docs/current-goal-state.md docs/goal/task-board.yaml \
  docs/design/pcc-gpu-next-work.md
found the routed entries
```

## Claim Boundary

This is a route/task-board hardening slice only. It does not implement new GPU
runtime behavior, ds4 support, pcc1 GPU launcher execution, five-GC GPU
lifetime parity, `.metallib` production, training, distributed execution, or
whole-program GPU execution.
