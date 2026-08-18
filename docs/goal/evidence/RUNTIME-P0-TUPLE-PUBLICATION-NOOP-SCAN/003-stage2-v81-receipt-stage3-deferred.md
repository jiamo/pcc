# Tuple completion scan: source-frozen Stage2 v81 receipt; Stage3 deferred

Date: 2026-09-06. Status: Stage1 -> Stage2 qualified on the frozen v81
source with the tuple guard; Stage3 explicitly deferred by the human
("先别 stage3, 主力优化 stage2 stage1") in favour of Stage2/Stage1
performance work.

## Command

```bash
gtimeout 720s env -u LC_ALL uv run python scripts/run_pcc_stage2_from_receipt.py \
  --stage1-dir build/tuple-noop-scan-stage1-v81 \
  --output-dir build/tuple-noop-scan-stage2-v81 \
  --stage2-timeout 600 --smoke-timeout 60 --self-backend-jobs 2 \
  --prediction-state build/preload-identity-stage2-v80/stage2
```

Same recipe as v80 (GC0, self backend, no libpython, frontend auto,
self-backend 2, link 8, 8 GiB tree guard, 600 s watchdog, cache off, private
pycache). Receipt: `build/tuple-noop-scan-stage2-v81/stage2-record.json`,
`manifest.json` status `COMPLETE`, log `build/tuple-noop-scan-stage2-v81.launch.log`.

## Result (single arm, wall is an observation, not a paired verdict)

```text
                        v80 (pre-fix)        v81 (tuple guard)
wall incl. barrier      566.617 s            536.959 s
compile wall            556.455 s            526.671 s
compile user CPU        1788.939 s           1650.585 s   (-7.7%)
compile sys CPU         203.263 s            189.887 s
publish barrier         10.143 s             10.267 s
sampled tree peak       8,031,649,792 B      8,030,470,144 B
pcc2 sha256             8e2f7ea6...          1f6f7c2d...
linkage                 libSystem only       libSystem only
Stage1 wall             185.70 s             187.21 s
Stage2 / Stage1         3.05                 2.87
```

Deferred-phase receipts (`stage2/pcc2.pcc-codegen-plan.result.json`,
`.link-profile.json`, `stage2/profile/stage2.json`):

```text
phase                   v80          v81         delta
coordinator checkpoint  132.399 s    134.575 s   +2.2 s
frontend (workers)      103.920 s    103.600 s   -0.3 s
ASM emit                119.876 s    119.179 s   -0.7 s
PCO emit                126.674 s     98.456 s  -28.2 s  (-22%)
pcc-owned link           72.665 s     70.033 s   -2.6 s
```

The whole-stage movement is the PCO lane, matching evidence 002's
receipt-bound worker replays (py_ast -25% instructions, population 1.126x).
The ASM lane and coordinator are unchanged, so the tuple scan is not the
owner of the remaining 2.87x gap.

## Supported claim

The tuple guard transfers through a real source-frozen pcc1 -> pcc2 Stage2 on
GC0 with a runnable libSystem-only pcc2, unchanged peak memory, lower CPU and
an exact PCO-lane reduction. Combined with evidence 002 this proves exit
criteria 1-3 and the Stage1 -> Stage2 part of criterion 4.

## Not proven / deferred

Stage3 (pcc2 -> pcc3) and the normalized pcc2/pcc3 comparison were not run;
the human deferred them on 2026-09-06 to prioritise Stage2/Stage1
performance. No fixed-point, parity, GC1..4 stage or GC4-linearity claim.
The row therefore closes as `DONE_WEAK` with the Stage3 boundary recorded.
