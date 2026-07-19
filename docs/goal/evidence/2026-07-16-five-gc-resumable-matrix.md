# 2026-07-16 resumable five-GC matrix evidence

Task: `M3-FIVE-GC-MATRIX-PERF`

## Result

The serial five-GC fixed-point gate now resumes only strictly verified work and
ends with a real aggregate pytest summary:

```text
gtimeout 1800s env -u LC_ALL uv run pytest -q \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py

5 passed in 368.45s (0:06:08)
```

The post-run process scan found no surviving pytest, `bootstrap.sh`, `pcc1`,
`pcc2`, or `pcc3` child.

## Restart-safe design

`tests/python/test_pcc_bootstrap_full.py` now records atomic JSON success
manifests at two boundaries:

- complete backend: pcc1/pcc2/pcc3 plus stage2/stage3 results;
- complete stage2: pcc1/pcc2 plus the stage2 result and the exact worker plan.

Reuse is content-addressed by the pcc/bootstrap source tree, shared pcc1,
pcc-Python runtime archive, platform/machine, GC backend, libpython/IR/runtime
mode, and (for stage2) frontend/self-backend worker allocation and CPU IDs.
Every hit re-hashes the output binaries and result JSON.  It also rechecks
executability, shared-stage1 identity, no-libpython, successful publish
barriers, and, at the full-backend boundary, normalized pcc2/pcc3 identity.
Missing, stale, partial, timed-out, mismatched-plan, or tampered results miss;
`PCC_BOOTSTRAP_FULL_REBUILD=1` bypasses both caches.

The focused cache/scheduling gate passed:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_gc_bootstrap_xdist_group.py \
  tests/python/test_pcc_bootstrap_full.py \
  -k 'bootstrap_gc or success_manifest'

10 passed, 6 deselected in 0.25s
```

The regression tests cover binary tampering, source fingerprint changes,
missing/timed-out stage result records, forced rebuild, execution-plan drift,
and the stage2-hit path executing stage3 without repeating stage2.

## Interruption evidence

The first corrected 1800-second run completed GC0/1/2 and timed out during
GC3.  Those complete same-source results were adopted only after the manifest
validator accepted them.  A resumed run completed GC3 and GC4 stage2 before
the aggregate watchdog interrupted GC4 stage3; GC0..3 remained complete and
GC4 did not receive a full-backend manifest.  The stage2 checkpoint then
validated GC4's successful stage2, and the closing run performed only GC4
stage3 after revalidating all prior boundaries.

Both timed-out process groups were reaped and both post-timeout scans were
empty.  No interrupted backend was represented as a pass.

## Claim boundary

This proves a restart-safe same-source/same-mode path to one five-test
no-libpython self-backend summary without rebuilding already verified stages.
It does not claim a cold, cache-empty five-backend matrix finishes within 1800
seconds, nor that build-directory manifests are portable release artifacts.

