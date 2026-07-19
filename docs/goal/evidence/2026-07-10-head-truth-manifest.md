# HEAD truth manifest evidence

Date: 2026-07-10

Task: `M0-HEAD-GATE-MANIFEST`

## Source identity

The checked machine-readable record is
`docs/goal/head-truth-manifest.json` (`pcc.head_truth.v1`). It records:

```text
commit: 81a252d882d84b601368b154d8cf34d3b40f8056
worktree_dirty: true
worktree_fingerprint: d677c6e5c7c6dfaf4e09042fe3753a0d8982a60a5863844825f463367af8e28d
platform: Darwin 25.5.0 arm64
python: 3.13.2
complete: true
claimable_commit: false
```

The dirty flag is deliberate claim hygiene. This run proves the exact recorded
worktree, not a clean GitHub commit status. Publishing clean-commit evidence is
owned by `M0-GITHUB-STATUS-CHECKS`.

## Required gates

```text
fallback-ratchet: PASS, 21 passed in 207.65s
control-plane-ratchets: PASS, 33 passed in 0.32s
gc-production-contract: PASS, 140 passed in 28.35s
llvm-bootstrap: PASS, links_libpython=false, pcc2_pcc3_equal=true
self-five-gc-bootstrap: PASS, 5 passed in 377.42s
```

The self artifact inspection records GC backends 0, 1, 2, 3, and 4
individually. Every pcc1/pcc2/pcc3 chain has `links_libpython=false` and
`pcc2_pcc3_equal=true`.

## Validation

```text
gtimeout 30s env -u LC_ALL uv run python scripts/head_truth_gate.py validate \
  build/head-truth/manifest.json --require-complete
OK: build/head-truth/manifest.json
```

The runner rejects timeouts, nonzero exits, missing final pytest summaries,
skips/xfails, missing bootstrap artifacts, libpython linkage, and pcc2/pcc3
drift before a gate can be recorded as PASS.
