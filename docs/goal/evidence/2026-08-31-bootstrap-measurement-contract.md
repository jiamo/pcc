# Bootstrap measurement contract closure

Task: `PERF-P0-BOOTSTRAP-MEASUREMENT-CONTRACT`

## Source and retained artifacts

- Baseline (No.100-v13) source:
  `6ce3234efeb22733ee3090e197edd62184e1687705d7a6299c1ba3853c1faa1b`.
- Candidate source:
  `dce8099e33d47567d347881884ebb0b79c149f9906cac6f78aec37174935f60b`.
- Runtime archive:
  `cd7f0175d0de0319a655b3b63af3ba1a7cc9a6fbf27404072ed09d7b94e7d804`.
- Combined receipt: `build/stage-ab-ruler-v13-current-combined.json`.
- Alternating receipts: `build/stage-ab-ruler-v13-current-pair1/manifest.json`
  and `build/stage-ab-ruler-v13-current-pair2/manifest.json`.

The first pair ran baseline then candidate; the second ran candidate then
baseline. Both completed. Every Stage1 private cache started empty outside the
frozen source tree and produced 338 `.pyc` files. Every pcc1/pcc2 passed the
runnable publish barrier and linked only libSystem, with no libpython or LLVM.

## Implemented contract

- `_measurement_env` keeps a per-arm private `PYTHONPYCACHEPREFIX` and removes
  inherited `PYTHONDONTWRITEBYTECODE`. The first timed process pays for writes;
  later workers reuse only artifacts made by that measured arm.
- Stage1 `user+sys` is timed-command plus waited-child tree CPU. Stage1
  instructions/cycles and footprint are coordinator-only diagnostics. Stage2
  process-tree RSS and timed tree CPU retain distinct scope labels.
- `scripts/run_pcc_stage_ab.py` owns the performance lock, freezes both sources,
  runs adjacent alternating Stage1/Stage2 arms, validates artifacts and emits
  one receipt. It forbids a single-wall verdict and reports
  `MEASURED_NO_AUTOMATIC_ACCEPTANCE`.

## Gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_pcc_compile_ab_tool.py \
  tests/python/test_pcc_stage1_build_tool.py \
  tests/python/test_bootstrap_performance_manifest.py

48 passed in 3.95s
```

The real summary-worker differential is retained in
`docs/investigations/stage1-private-pycache-disabled-worker-startup.md`:
bytecode-disabled starts took 0.562--0.620s; the same worker with its private
warm writable cache took 0.106--0.112s. No source-tree bytecode was written.

## Paired observations

| Stage | Arm | wall | tree CPU | peak tree RSS |
|---|---|---:|---:|---:|
| Stage1 | v13 | 176.765s | 832.835s | 6.181GB |
| Stage1 | current | 159.640s | 732.570s | 6.172GB |
| Stage2 | v13 | 441.512s | 2608.031s | 20.780GB |
| Stage2 | current | 432.013s | 2505.685s | 19.724GB |

Paired-median candidate/baseline ratios were Stage1 wall `0.9073`, Stage1 tree
CPU `0.9014`, Stage2 wall `0.9814`, Stage2 tree CPU `0.9639`, and Stage2 peak
tree RSS `0.9490`. Individual pair ratios varied materially, confirming why a
single wall or lowest sample cannot accept a source change.

## Supported claim

The measurement environment and metric scopes are comparable, receipt-bound
and reusable. Current source is modestly better than v13 under paired medians,
but this does not prove Stage2 <= Stage1, pcc1 faster than host, Stage3, a fixed
point, or GC1--4. Those remain downstream tasks.
