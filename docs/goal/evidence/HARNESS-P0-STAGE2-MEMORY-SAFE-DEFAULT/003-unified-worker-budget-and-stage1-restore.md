# 003 — Unified worker-budget scheduler + host Stage1 restoration (v11)

Date: 2026-08-31. Follows evidence 002 (prediction). Implements the unified
admission formula, restores host Stage1 width, and lands the mechanical
"prediction must fit the contract" gate.

## Code changes

- `pcc/py_frontend/pipeline_frontend_workers.py`: one admission formula for
  every executor — `budget_jobs(cpu, memory_budget, per_worker_peak, hard_cap)`
  = min(cpu, hard risk cap, (budget − 1 GiB coordinator reserve) / measured
  per-worker peak). Executor differences enter only through measured peak
  constants (host CPython frontend worker 2 GiB conservative vs 1.7 GiB
  measured; compiled pcc1 safe-band worker 3 GiB vs ≤2.5 GiB measured).
  Budget arrives via `PCC_WORKER_TREE_BUDGET_BYTES`; absent budget preserves
  the historical cpu/cap behavior. `compiled_native_auto_jobs` keeps its
  exported signature and its ≤2 hard cap — a budget can only shrink compiled
  admission, never widen it.
- `pcc/py_frontend/pipeline_frontend_parallel.py`: the codegen lane's
  oversized split + safe-lane clamp now uses the native-only predicate
  (`native_auto_source_lanes`), mirroring the export lane. Host CPython
  workers keep full chunked width in auto mode; compiled pcc1 workers keep
  the split + ≤2 clamp unchanged. Both files pass the 30s no-libpython
  closure check.
- `scripts/run_pcc_stage1_build.py`: `--jobs` defaults to `auto`, resolved by
  the same `budget_jobs` (host peak constant, cap 10); `--memory-budget-bytes`
  defaults to half of physical RAM (96 GiB machine → 48 GiB → jobs 10).
- `scripts/run_pcc_stage2_from_receipt.py`: new `--prediction-state DIR`
  computes `predict_stage2_seconds` from a retained partial run's plan state
  (largest-first byte-fraction scaling + 230s measured checkpoint/link
  reserve) and REFUSES the run when the prediction exceeds
  `--stage2-timeout`. On the real v5 state it predicts 1302.8s and refuses
  the 600s contract, matching evidence 002.

## Tests (all green, `-x -n0`)

- `tests/python/test_pipeline_frontend_workers.py` (budget formula, host auto
  budget derivation, compiled cap never widened) — 12 passed.
- `tests/python/test_pipeline_frontend_worker_owners.py` (host auto unsplit
  [(4,2)] / [(2,2)]; native auto split fail-closed [(1,1)]) — with
  `test_py_multi_file_compile.py`, `test_stage2_from_receipt_tool.py`,
  `test_process_tree_sample_tool.py`, `test_bootstrap_performance_manifest.py`:
  97 passed.
- `tests/python/test_pcc_stage1_build_tool.py` (auto resolution, budget
  ceiling) — 7 passed.
- `tests/python/test_pcc_bootstrap_full.py -k 'matrix_plan or resource or
  process_tree or stage_sampler or bootstrap_defaults'` — 4 passed.
- Commit-level: `test_bootstrap_gate_baseline.py` 2 passed;
  `test_fallback_baseline.py` + `test_ir_py_fallback_baseline.py` 17 passed
  (exit 0).

## Measured result — Stage1 v11 (current source incl. these fixes)

Snapshot `/private/tmp/pcc-memory-safe-source-v11` (read-only, tracked
worktree files), receipts under `build/memory-safe-stage1-v11*`:

- compile wall **161.62s** (user 758.53s, sys 34.97s) vs v10's 369.13s at
  hand-forced jobs=2 — 2.28x, beating the ≤212s restoration target.
- jobs resolved to 10 by the formula (counters: multi_frontend_jobs=10,
  40 chunks, worker concurrency 10); frontend codegen 303.5s → 95.9s.
- peak tree RSS **4.6 GiB** at 10 workers (24 GiB sampler cap, preflight
  clean, no surviving child) — host chunked workers measure ~0.3–0.4 GiB
  each, far below the conservative 2 GiB constant.
- pcc1: 221,938,168 bytes, sha256 cde1efc9e726fc4e…, otool shows only
  `/usr/lib/libSystem.B.dylib`, function smoke compiled and ran (`42`).
- remaining wall owners at jobs=10: codegen lane 95.9s, pcc-owned link
  driver 49.7s — these are the levers for the 100–130s stretch goal and the
  pcc1-cost work (task list item 4).

## Mode labels

Host pcc0 Stage1, self backend, no-libpython, gc0, direct-indexed emit, cold
private caches. This proves host Stage1 restoration only — no Stage2, no
fixed-point, no five-GC claim.
