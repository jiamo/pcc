# Bootstrap phase reuse: cold baseline and bootstrap.sh cache wiring

Date: 2026-07-31

Task: `PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE`

## Source identity

Base commit `98e62890963c60515d6f8ddc8c31996b04500f95` plus this session's
slices. Changed behavior in this slice:

- New host-side module `pcc/bootstrap_cache_identity.py` owning the two cache
  namespaces (`bootstrap_source_sha256` for the frontend IR cache,
  `bootstrap_object_cache_identity` for the self-backend object cache) with a
  `python -m` entry printing both for shell consumption.
- `tests/python/test_pcc_bootstrap_full.py` delegates its identity/source-hash
  helpers to that module (same values by construction; 25 unit tests pass).
- `scripts/bootstrap.sh` derives both identities when the caller has not
  namespaced the run and defaults
  `PCC_SELF_BACKEND_OBJECT_CACHE_DIR` to the shared
  `build/bootstrap-pytest-object-cache`, so suite provisioning, gate chains,
  and manual bootstraps reuse one content-addressed cache.
  `PCC_SELF_BACKEND_OBJECT_CACHE=0` still disables both caches.

## Exit criterion 1: phase-level cold baseline (isolated, no concurrent suite)

Isolated `pcc1 -> pcc2` (stage2 only, `--reuse-stage1`, fresh run-cache and
fresh/disabled compiler caches, current fresh pcc1, self backend,
no-libpython, `PCC_PY_FRONTEND_JOBS=auto`):

```text
wall 438.6s   user 579.2s   sys 48.1s   peak RSS 5.93 GB   modules 154
dominant phase: link_self_emit_objects_native 374,168 ms (85% of stage)
  (serial: link_self_native_emit_jobs=1 — the documented RSS-ceiling guard
   serializes native emission when any module exceeds the split threshold)
frontend codegen: multi_frontend_codegen_parallel 45,079 ms (10 workers)
caches: frontend IR 0 hit / 0 miss, object cache 0 hit / 0 miss (disabled)
profile: build/bootstrap-perf-phase-baseline-20260731/profile/stage2.json
```

## Exit criteria 2-3: content-addressed reuse across equivalent invocations

Same stage2 through `scripts/bootstrap.sh` after the wiring (fresh out dirs
and fresh run-cache dirs per run; shared object/IR cache namespace):

```text
run A (frontend IR miss, object cache warm from today's suite/warmup):
  stage2 78.2s, 273 object hits / 0 misses, RSS 1.71 GB
run B (equivalent invocation, both caches hit):
  stage2 29.87s   (93.2% faster than the 438.6s cold baseline)
  link_self_emit_objects_native: 374.9s cold -> 11.6s
  peak RSS 2.15 GB (cold baseline 5.93 GB; documented ceiling 8 GiB)
```

Correctness: the cold-built, populate-run, and full-reuse `pcc2` binaries are
byte-identical (`sha256 a4b01b30cda0...72b0`, `cmp` clean), so the reuse path
preserves the fixed-point discipline. No timeout was changed.

Gate: `tests/python/test_py_frontend_ir_pass_pipeline.py -k 'cache or
profile or deterministic'` — 4 passed. `tests/python/test_pcc_bootstrap_full.py`
non-integration contracts — 25 passed.

## Not proven / remaining boundary

- Exit criterion 4 remains open: the forced-rebuild five-GC matrix, the exact
  six-worker non-integration suite (last final summary 1044.91s > 900s), and
  the exact integration suite need final summaries inside 900 seconds.
- The stage1-rebuild-on-consecutive-invocations question from the previous
  boundary is resolved by observation: after the warmup-lane fix, a suite run
  leaves no `pcc/` source newer than `build/bootstrap/pcc1`, and consecutive
  invocations reuse stage1 (see
  `2026-07-31-cold-warmup-full-suite-closure.md`).
