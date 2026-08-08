# Cold self-host warmup budget: broad-suite closure

Date: 2026-07-31

Task: `TEST-P0-COLD-SELF-HOST-WARMUP-BUDGET`

## Source identity

Base commit `98e62890963c60515d6f8ddc8c31996b04500f95` plus the
`PKG-P1-RUNTIME-PROFILE-ENVIRONMENT-INVARIANCE` slice
(`pcc/package/runtime_profile.py` new, `pcc/package/uv_lock_sync.py`
modified). Because `pcc/` content changed, the stage caches were genuinely
cold for the current source: this run exercises exactly the cold-fixture
scenario the row's open boundary demanded, not a warm-cache rerun.

## Commands and results

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_test_infrastructure_efficiency.py
21 passed in 0.69s

gtimeout 590s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_self_host_oracle_diff.py::test_000_self_host_oracle_stage_cache_warmup
1 passed in 219.57s (0:03:39)        # genuinely cold current-source chain

gtimeout 1200s env -u LC_ALL uv run pytest
9602 passed in 1044.91s (0:17:24)    # exact six-worker non-integration suite
# post-run: ps found zero surviving pytest/pcc1/pcc2/pcc3 children
```

The cold warmup ran under a 590s outer watchdog (stricter than the listed
1200s ceiling) and completed in 219.57s. The full suite then completed with
a final summary in 1044.91s — faster than the 1125.63s pre-change clean-HEAD
run recorded in
`docs/goal/evidence/2026-07-31-uv-locked-native-sync-full-suite-closure.md`,
despite the cold stage caches, because the warmup lane had already published
the current-source stage artifacts.

## Exit criteria mapping

1. Fixed inner frontend budget independent of outer worker count: enforced by
   the infrastructure contract gate (21 passed).
2. Cold pcc2/pcc3 warmup completes inside the bounded stage and command
   watchdogs and publishes the immutable artifacts: 219.57s on genuinely
   changed source, final summary present.
3. The exact six-worker non-integration suite completes with a final pytest
   summary and no surviving compiler or pytest children: 9602 passed in
   1044.91s, leftover-children count 0.

## Supported claim

The bounded self-heavy lane prevents the cold fixture cascade under
complete-suite contention on darwin-arm64: a current-source cold chain warms
inside its per-stage budget and the following six-worker suite finishes with
a final summary and no leftover children. After the run, no `pcc/` source is
newer than `build/bootstrap/pcc1`, so consecutive suite invocations reuse
stage1 instead of rebuilding it.

## Not proven

- The complete integration suite is not part of this row's gates (its most
  recent final summary is 4555 passed in 817.52s on the pre-change tree).
- The 900-second non-integration target belongs to
  `PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE` and remains open: 1044.91s > 900s.
