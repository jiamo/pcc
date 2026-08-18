# HARNESS-P0 safety exits #1-3 proven; only exit #4 (memory fit) remains

## Fresh evidence: the default compiled Stage2 fan-out is bounded to 2

From the capped Stage2 profile (build/inline-edge-stage2-capped-v2, a pcc1
compiled WITH the new allocator, run under run_pcc_stage2_from_receipt.py
--max-tree-rss-bytes 8589934592, killed at MEMORY_LIMIT ~152s):

```text
multi_frontend_export_safe_jobs:   2      (NOT the incident's 10)
multi_frontend_codegen_safe_jobs:  2      (NOT the incident's 8)
multi_frontend_codegen_oversized_jobs: 1  (oversized inputs serial)
```

So exit #1 ("default pcc1 Stage2 cannot launch a 10-worker frontend/export or
8-worker self-backend/link wave") is met: `compiled_native_auto_jobs`
(pipeline_frontend_workers.py) bounds the compiled lane via
`budget_jobs(..., COMPILED_SAFE_WORKER_PEAK_BYTES, SOURCE_WORKER_AUTO_SAFE_JOBS)`;
the 29-test `test_pipeline_frontend_workers.py` +
`test_pipeline_frontend_worker_owners.py` gate is green.

Exit #2 ("every ordinary bootstrap compiled stage is launch-preflighted and
externally hard-capped at 8 GiB/600 s"): scripts/bootstrap.sh wraps each stage
in `run_process_tree_sample.py --max-tree-rss-bytes ${BOOTSTRAP_MAX_TREE_RSS_BYTES}`
(default 8589934592) with `--darwin-preflight-reserve-bytes`, via
`run_pcc_deferred_link.py`.

Exit #3 ("receipt records full argv + worker-manifest ownership for the largest
process"): the process-tree receipt records `largest_pid` / `largest_command`
per sample and the manifest error line names the largest worker's full argv
(e.g. pid 47825 = the plan-state coordinator, 6.55 GiB).

## Only exit #4 remains, and it is blocked on the native-data-plane row

Exit #4 ("a current-source Stage2 completes below 8 GiB and produces a runnable
libSystem-only pcc2; otherwise the row remains active") is NOT met: the fresh
capped Stage2 hit MEMORY_LIMIT at 8.24 GiB tree / 6.55 GiB largest process, the
fan-out already at the safe 2.  Per RUNTIME-P1 evidence 003 the ~6.5 GiB is
LIVE working set (direct-indexed-kernel capture/arenas + assembler section/
relocation graph), MONOTONIC (0 reclaim drops), so neither the safe fan-out nor
the (now-built, measured-ineffective) allocator slab trim can bring it under
8 GiB.  Exit #4 therefore depends entirely on reducing the per-worker/
coordinator live memory, which is the IN-PROGRESS IMMEDIATE row
PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT (builder->emitter native data
plane; drop the indexed-kernel capture per-module / free assembler sections
before encoding).  The safety scaffolding this row owns is complete; the memory
fit is not this row's to produce.
