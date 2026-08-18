# 005 — Stage2 v8 (v15 pcc1) MEMORY_LIMIT classification and corrected prediction

Date: 2026-08-31. Follows evidence 004. Receipts: `build/memory-safe-stage2-v8*`.

## Outcome

The single authorized capped Stage2 (600s / 8 GiB, jobs=2, v15 pcc1) tripped
the **memory breaker at 537.1s**: tree RSS ramped 7.07 → 9.23 GiB in ~0.9s
(observed peak 8.59 GiB), rc -15, whole tree killed, no partial pcc2, 28/224
deferred results retained. Containment behaved exactly as designed.

(The first attempt, v7, failed closed in 0s: the stage1 receipt's host
`PCC_BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS=10` leaked into bootstrap.sh's
compiled-stage worker-budget guard. Fixed by the missing `auto` override in
`_stage2_environment_overrides`; 13 focused tests green. v5/v6 never saw
this because their stage1 was also hand-throttled to 2.)

## Two defects the receipts prove

1. **Memory axis — hardcoded lane widths + wave scheduling.**
   `scripts/run_pcc_deferred_link.py` runs the deferred codegen lanes at
   fixed widths (serial 1 / paired-oversized 2 / heavy 2 / medium 3 /
   **small 4**) in waves (a whole batch must finish before the next
   launches). The small band is classified by AST BYTES, but the layer1
   generated-heavy family (e.g. `_l1_codegen_static_methods`, 43KB source)
   balloons to 3–4 GiB per worker at codegen; 3–4 such workers admitted
   concurrently crossed 8 GiB. Largest single process at death was only
   4.08 GiB — the trip was aggregate, exactly the hardcoded-width anti-pattern
   the unified budget scheduler exists to remove.
2. **Time axis — the frontend-only canary bias.** The class_gen replay
   canary (evidence 004) measured the frontend half only (no ASM capture in
   the standalone env). v8's real per-module completions include the
   self-backend emit: cli_bootstrap ran the serial lane alone in **64s**
   (frontend model said 15s); class_gen took ~40s wall (frontend 11s + emit
   ~29s). The 561s prediction was therefore systematically low.

## Corrected prediction (from v8's own 28 full-cost completions)

`predict_stage2_seconds(build/memory-safe-stage2-v8)`:
28/224 modules = 49.3% of source bytes in 390.7s of lane →
lane ≈ **792s**, whole Stage2 ≈ **1022s** at current widths/costs.
Progress vs the v5-era 1303s (the 2.1x frontend win is real), but the 600s
contract still needs ~1.7x, now owned by:

- the self-backend emit half of each worker (known huge-module O(N²)
  investigations are prior art), and
- lane scheduling waste (wave stragglers) — fixable together with the
  memory axis.

## Named next slices

- **A (memory, structural):** replace the deferred driver's waves with a
  sliding window plus a live aggregate-RSS admission gate derived from
  `PCC_WORKER_TREE_BUDGET_BYTES` (default 8 GiB): admit the next worker only
  when live children RSS + a per-launch reserve fits the budget. Widths
  become caps, not schedules; the breaker becomes unreachable by
  construction and stragglers stop serializing the lane.
- **B (time):** measure the emit half with the same single-module replay
  discipline (requires the plan-driver capture env), then run the candidate
  loop against the emit cost. No Stage2 rerun until the refreshed prediction
  fits 600s.

## Mode labels

pcc1(v15) compiled Stage2, self/no-libpython, gc0, 8 GiB external cap. This
classifies one MEMORY_LIMIT; it proves no Stage2 completion and no fixed
point.
