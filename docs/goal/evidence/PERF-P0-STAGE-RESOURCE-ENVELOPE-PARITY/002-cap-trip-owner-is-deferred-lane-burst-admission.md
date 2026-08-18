# 002 — the 8 GiB trip is deferred-lane burst admission, not the coordinator

Date: 2026-09-03

## Claim boundary

Receipt re-read plus a harness fix with focused tests.  No Stage1, Stage2 or
GC run was started in this slice; the fix is NOT yet proven on a real capped
Stage2 (that run needs explicit authorization, see "Not proven").

## What the receipt actually says

Source: `build/inline-edge-stage2-capped-v2/stage2-process.{result.json,
samples.tsv}` (the RUNTIME-P1 evidence-003 "new allocator" arm; 1807 samples
at 0.25 s; cap 8589934592; status MEMORY_LIMIT; returncode -15; elapsed
564.3 s; peak tree 8852242432 = 8.24 GiB).

Tree RSS per 10 s bucket (max in bucket; largest process named):

```text
   0-150s  coordinator (pcc1 ... pcc/__main__.py -o pcc2) climbs 0.7 -> 6.55 GiB
           tree peak 7.15 GiB at 120s, 9-10 processes (export workers)
           largest single process ever observed: 7.03 GiB at 152s = coordinator
   152s    coordinator exits; tree drops to ~2 GiB
 160-225s  serial lane: worker_0 (class_gen, AST 13.9 MB) alone -> 6.04 GiB
 225-338s  paired lane: pairs of 3.0-4.0 GiB workers, tree <= 6.26 GiB
 338-453s  heavy lane (AST 3.2-4.4 MB): 2.1-4.2 GiB each, tree <= 5.73 GiB
 453-553s  medium lane (AST 2.0-3.0 MB): 1.3-2.7 GiB each, tree <= 5.2 GiB
 553s      small lane starts: width 4 admits worker_27/28/32/33 in ONE poll
 554-564s  those four grow together: 2.75 + 2.56 + 1.58 + 1.34 GiB at kill,
           all still growing -> tree 8.24 GiB -> MEMORY_LIMIT at 564.3 s
```

Kill-time process list (result.json `terminal_processes`): worker_32 2.75 GiB,
worker_27 2.56, worker_33 1.58, worker_28 1.34, driver python 0.02, bash 0.00.
The same shape appears in the earlier v12 arm (envelope evidence 001 update:
"at kill 4 concurrent codegen pcc1 workers summed ~8.6 GB").

So two board claims are wrong and are corrected here:

- "the capped Stage2 owner is the pcc1 COORDINATOR whose RSS climbs to
  ~6.5 GiB" — the coordinator IS the largest single process, but its phase
  peaked at 7.15 GiB tree and exited under the cap.  It never tripped the
  breaker.
- "killed at MEMORY_LIMIT ~152s" (HARNESS-P0 evidence 010) — 152 s is when
  the largest process was observed; the kill was at 564 s.

Per-worker peak (RSS while it was the tree's largest process; a lower bound):

```text
lane     AST MB      peak GiB   examples
serial   13.89       6.04       worker_0
paired   6.5-8.0     3.0-4.0    worker_1 4.00, worker_4 3.87
heavy    3.2-4.4     2.1-4.2    worker_12 4.21 (3.21 MB), worker_8 2.56
medium   2.0-3.0     1.3-2.7    worker_14 2.69, worker_50 2.39
small    1.75-1.92   1.9-2.75+  worker_32 2.75 and worker_27 1.88, growing
```

Lane membership (AST bands 3 MB / 2 MB): serial 1, paired 6, heavy 8,
medium 16, small 193.  31 of 224 modules had results at the kill: every
non-small lane had finished; the small lane had just started.

## Mechanism (source-visible)

`scripts/run_pcc_deferred_link.py::_run_codegen_batches` admitted a launch
when `live + 2 GiB <= 7 GiB` and then `continue`d to the next poll without
sleeping.  A fresh pcc1 worker reads near-zero RSS on that next poll, so the
check passed again; the whole `width` filled within milliseconds, and the
2 GiB launch reserve covered only ONE worker's growth.  The pressure ladder
then stopped one worker per 0.2 s poll, which cannot outrun four workers
growing ~0.3-0.5 GiB/s each inside the 1 GiB soft-to-hard margin.

## Code change (same file)

- `_live_rss_by_pid` replaces the aggregate read; each running worker is
  charged `max(live RSS, floor)`.
- `floors[i]` = expected peak of manifest i.  Heavy/medium/small lanes pass
  an AST-derived floor `min(0.9 GiB + 1.2 GiB per AST MB, 3.5 GiB)`
  (1.9 MB -> 3.18 GiB, above the 2.75+ observed; two capped floors still fit
  7 GiB so heavy/paired keep width 2; a 0.4 MB module floors at 1.4 GiB so
  the tiny tail keeps width 4).  Serial/paired lanes keep the 2 GiB default.
- Admission: `charged + floor(new) <= 7 GiB` (budget minus the 1 GiB driver
  reserve); the empty-window progress guarantee is unchanged.
- Ladder: when live RSS exceeds the soft ceiling, every runnable worker but
  the oldest is SIGSTOPped in the same poll (the youngest have the most
  growth left).  Resume keeps the 2 GiB hysteresis on the charged sum.
- Every lane's admission stats (launched, admission_denied, suspensions,
  resumes, peak_live_bytes, peak_charged_bytes) are written into the codegen
  result receipt `<plan>.result.json` under `lanes.<lane>.admission` — this
  is the row's exit criterion "every admission/stop decision is recorded".

## Focused evidence (`-x -n0`)

```text
tests/python/test_deferred_link_window.py (11) + test_deferred_self_link_tool.py (5)
16 passed in 5.96s
  new: fresh workers charged their floor (3 GiB floors -> max 2 concurrent,
       peak_charged 6 GiB, admission_denied >= 1); 1 GiB floors never deny
       width 4; floors must align; floor model bounds; ladder stops all-but-
       one in one poll (suspensions == 2 with three running).
  fixed: test_frontend_codegen_plan_runs_worker_then_ordered_link was red
       at the current worktree (FakeProcess lacked pid/poll/send_signal
       after the window landed); fixture completed.
tests/python/test_pipeline_frontend_workers.py test_bootstrap_performance_manifest.py
tests/python/test_process_tree_sample_tool.py test_stage2_from_receipt_tool.py
39 passed in 1.13s
```

## Not proven / what this does to the timeline

- The fix has not run a real capped Stage2.  HARNESS-P0's failure
  disposition requires explicit authorization for each capped run; none was
  given in this session, so none was started.
- Wall: at the 564 s kill only 31/224 modules were done; the small lane
  (193 modules, AST sum 112.7 MB) had just begun.  A rough model (per-module
  cost 0.94 s + 11.9 s per AST MB, from the v15 full-cost fit) puts the small
  lane at ~550 s under floor-charged admission (~380 s at the old unsafe
  width 4, ~1520 s serial).  Whole Stage2 therefore projects to roughly
  1100 s+ (coordinator 152 + lanes ~950 + link), so the next capped run WILL
  hit `--stage2-timeout 600` before it proves the memory fit.  The 600 s
  value is a contract, not a cap; raising the timeout for one diagnostic
  memory-proof run is a human decision this evidence asks for.
- The coordinator's 6.5-7.0 GiB live set is real and stays the
  native-data-plane row's wall problem; it is no longer cited as the blocker
  of the memory fit.
