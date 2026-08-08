# Bootstrap phase reuse: criterion 4 closure (matrix + both suites in budget)

Date: 2026-07-31

Task: `PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE`

## Source identity

Base commit `98e62890963c60515d6f8ddc8c31996b04500f95` plus this session's
slices (bootstrap cache wiring `pcc/bootstrap_cache_identity.py` +
`scripts/bootstrap.sh`, runtime-profile package contract, py_list raise
semantics + frontend err-checks, c_codegen implicit-declaration prototype
fixes). The tree was unchanged across the three gate runs below.

## Commands and results

```text
gtimeout 900s env -u LC_ALL PCC_BOOTSTRAP_FULL_REBUILD=1 uv run pytest -q \
  -m integration tests/python/gc/test_pcc_bootstrap_full_gc0.py ... gc4.py
5 passed in 363.17s (0:06:03)         # forced-rebuild five-GC fixed-point matrix

gtimeout 1200s env -u LC_ALL uv run pytest --durations=75
9604 passed in 1095.66s (0:18:15)     # first run after source changes:
                                      # durations show setup of the warmup
                                      # lane rebuilt cold stages for 490.70s
                                      # inside the suite

gtimeout 1200s env -u LC_ALL uv run pytest
9604 passed in 756.78s (0:12:36)      # exact suite, unchanged tree: <900s

gtimeout 1800s env -u LC_ALL uv run pytest -m integration
4555 passed in 786.99s (0:13:06)      # exact integration suite: <900s
```

Zero surviving compiler/pytest children after each run.

## Exit criteria mapping (completing 1-3 from
`2026-07-31-bootstrap-phase-reuse-cold-baseline-and-cache-wiring.md`)

4. The forced-rebuild five-GC fixed-point matrix (363.17s), the exact
   six-worker non-integration suite (756.78s on an unchanged tree), and the
   exact integration suite (786.99s) each completed inside 900 seconds with
   final pytest summaries; every backend and semantic gate remains present
   (no marker, timeout, or selection change in this task).

The >900s first-run reading is now understood and documented rather than
mysterious: a source change rotates the content-addressed cache namespaces,
so the first suite invocation on a changed tree performs one bounded
in-suite warmup rebuild (490.70s in the durations table); every following
invocation reuses the published stage artifacts. The previous boundary's
"stage1 rebuilt for four minutes on consecutive invocations" is thereby
closed: consecutive invocations on an unchanged tree rebuild nothing.

## Supported claim

Self-host stage compilation reuses GC-invariant frontend IR bundles and
native objects across equivalent invocations through one content-addressed
namespace shared by `scripts/bootstrap.sh` and the pytest bootstrap helper;
the dominant stage phase is 93.2% faster on reuse with byte-identical
output; and the repository's three heaviest gates all complete inside their
900-second targets with final summaries on darwin-arm64.

## Not proven

- Wall-clock behavior under sustained external host contention (the BOC
  row's quiet-host labeling discipline applies here too).
- The first invocation after a source edit still pays one in-suite warmup
  rebuild; eliminating that by warming caches at edit time is possible
  future work, not part of this row.
