# uv-locked native sync full-suite closure

Date: 2026-07-31

Task: `PKG-P1-UV-LOCKED-NATIVE-SYNC`

## Source identity

- Commit: `98e62890963c60515d6f8ddc8c31996b04500f95` (clean worktree before and
  after the runs; no source change in this slice)
- Platform: Darwin 25.5.0 arm64, host `uv run` Python entrypoints

## Boundary closed

The row's remaining open boundary asked for two things beyond the resolved
stale-wheel fixture slice (`2026-07-29-uv-locked-wheel-current-pcc1.md`):

1. The focused groups/extras/markers and fail-closed schema/source evidence
   must still hold. `tests/python/test_package_uv_lock_sync.py` covers the
   frozen-graph projection with groups/extras/markers, the target-specific
   fail-closed marker evaluator (`PCC-PKG-UVLOCK-MARKER-UNSUPPORTED`), the
   schema/target-python/incompatible-wheel/missing-artifact rejections
   (`PCC-PKG-UVLOCK-UNSUPPORTED-SCHEMA`, `PCC-PKG-UVLOCK-TARGET-PYTHON-MISMATCH`,
   `PCC-PKG-UVLOCK-INCOMPATIBLE-WHEEL`, `PCC-PKG-UVLOCK-MISSING-ARTIFACT`),
   transactional publish with unchanged-repeat zero downloads / zero native
   builds, and previous-environment preservation on install, build-isolation,
   and publish failures.
2. The exact six-worker non-integration suite and the complete integration
   suite must finish with final summaries.

## Commands and results

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_package_uv_lock_sync.py
7 passed in 0.29s

gtimeout 590s env -u LC_ALL uv run pytest -q -n0 -m integration \
  tests/integration/test_uv_locked_pcc_sync.py
2 passed in 277.06s (0:04:37)

gtimeout 1200s env -u LC_ALL uv run pytest
9582 passed in 1125.63s (0:18:45)

gtimeout 1800s env -u LC_ALL uv run pytest -m integration
4555 passed in 817.52s (0:13:37)
```

The integration lock-sync gate ran under a 590s outer watchdog instead of the
listed 900s ceiling; it completed in 277s with a final summary, so the gate's
budget is respected. No leftover `pytest`/`pcc1`/`pcc2`/`pcc3` children after
the runs.

## Supported claim

The versioned uv.lock adapter row is complete at its declared boundary:
`pcc sync --locked` consumes but never modifies `uv.lock`, records lock digest
and selected graph/artifact digests in the environment manifest, resolves
nothing independently, fails closed with stable diagnostics on unsupported
schema, target mismatch, missing artifacts, build isolation, and incompatible
wheels, publishes transactionally with zero-work unchanged repeats, and both a
generic locked dependency graph and a real locked NumPy project compile and
run from the uv-owned pcc overlay without libpython (host-provisioned
current-source pcc1, self backend). Both full suites have final green
summaries on this source identity.

## Not proven

- Broader package compatibility beyond the locked generic graph and NumPy
  project.
- pcc1→pcc2→pcc3 fixed-point behavior (not part of this row).
- Multi-platform (linux) lock projection; the evidence is darwin-arm64.
