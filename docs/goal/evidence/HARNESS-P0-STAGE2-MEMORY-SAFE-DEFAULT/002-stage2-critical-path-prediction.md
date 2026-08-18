# 002 — Stage2 critical-path and RSS-envelope prediction (read-only, from retained v5/v6 receipts)

Date: 2026-08-31. No new build or probe was run; every number below comes from
files already on disk under `build/memory-safe-stage2-v5/` and
`build/memory-safe-stage2-v6/`.

## Inputs

- `build/memory-safe-stage2-v5/stage2-process.result.json` — TIMEOUT at 600.2s,
  peak tree 7.31 GiB, rc -15.
- `build/memory-safe-stage2-v6/stage2-process.result.json` — INTERRUPTED
  (explicit human stop) at 450.4s, peak tree 7.30 GiB, rc -15, no surviving
  child.
- v5 plan state `pcc2.pcc-codegen-plan.state.95187`: 224-module manifest,
  21 completed `results/worker_*.tsv` (per-item mtimes give completion order).
- v5/v6 `stage2-process.samples.tsv`: 250 ms tree/largest-process RSS samples.
- `--identity-index` in `scripts/run_pcc_stage2_from_receipt.py` is only the
  receipt pair index (naming); there is NO ASM/PCO identity-reuse mechanism in
  the current worker lane. v6 carried no reuse.

## Measured shape of the v5 run (600s)

- 0–~140s: coordinator checkpoint phase; the run's RSS peak (7.31 GiB) is in
  this phase, largest single process 6.7 GiB (coordinator).
- ~150–600s: deferred worker lane at 2 workers; tree RSS 3.0–6.0 GiB; a single
  big-module worker peaks at ~6.0 GiB alone (observed 5.99 GiB while
  class_gen/type_infer were in flight).
- Lane completed 21/224 modules in ~450s. The plan schedules largest-first:
  those 21 modules are exactly the 21 largest sources and carry 3.2 MB = 43.0%
  of the 7.5 MB total source bytes.
- v6 (same shape, v10 pcc1) reproduced the same throughput: 13 items in ~270s
  of lane time, same ordering. Per-completion gaps range 0.3s (literal_lowering)
  to 51.7s (class_gen), so cost tracks module size; the fresh-process floor per
  item is small (~1–3s).

## Prediction

- Byte-throughput fit at jobs=2: lane total ≈ 450s / 0.43 ≈ **1046s**.
- Plus checkpoint (~150–180s) and pcc-owned link (~50s measured for the same
  224 modules in stage1 v10): **whole Stage2 ≈ 1250–1300s at jobs=2,
  peak tree ≈ 7.3–7.8 GiB (inside the 8 GiB cap, margin 0.2–0.7 GiB).**
- The 600s contract is infeasible for a cold full re-emit at any admissible
  concurrency: the big-module band alone (2 workers, ~6 GiB each peak, so no
  third can be admitted under 8 GiB) already needs ~450s, leaving ~0s of the
  post-checkpoint budget for the remaining 203 modules. Widening only the
  small band (<20 KB, 125 modules, 1.1 MB total) can recover at most ~10–15%
  of the lane, not the missing 2.1x.
- Verdict: **the v5/v6 timeouts were arithmetically necessary, not anomalies.
  Re-running the same shape under 600s can never complete.** The two exits are
  (a) run the single authorized capped Stage2 with a prediction-derived
  timeout ≈ 1500s (same 8 GiB cap, jobs=2), or (b) first reduce per-module
  pcc1 emit cost (the blocked PERF rows). (a) produces the row's required
  runnable pcc2 evidence and a real cost profile for (b).

## Also confirmed

- Stage1 v10 369.13s is not a host baseline: the compiled-stage jobs=2 memory
  policy was applied to host Stage1 (8 chunks / 224 modules, frontend codegen
  303.5s of 369s). Host stage1 tree peaked at 4.46 GiB at jobs=2, so a wider
  host-only scheduler fits the same 8 GiB budget; concurrency must be derived
  per-executor (memory budget / measured per-worker peak), not per-stage
  special case.
- Stage1 v9→v10 delta (+7.4s) was machine contention: identical 224-module
  inputs, identical instruction counts, +45% involuntary context switches.
