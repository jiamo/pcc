# CI truth workflow contract evidence

Date: 2026-07-10

Task: `M0-CI-WORKFLOW-CONTRACT`

## Change

- `.github/workflows/head-truth-light.yml` runs the registry's light suite on
  every push and pull request and uploads the manifest plus logs.
- `.github/workflows/head-truth-heavy.yml` is reusable, manual, and nightly. It
  runs `--suite all`, uploads evidence even on failure, and then requires a
  complete clean-commit manifest.
- `.github/workflows/workflow.yml` makes PyPI publication depend on the reusable
  heavy workflow.
- `scripts/head_truth_gate.py` now returns failure when any selected gate is not
  PASS under `--keep-going`.
- The light registry includes deterministic mode/claim intent locks instead of
  the artifact-dependent bootstrap baseline test, which legitimately skips when
  no prebuilt binaries exist.

## Gates

```text
pytest tests/test_goal_state.py tests/test_goal_startup_docs.py
       tests/test_head_truth_manifest.py tests/test_head_truth_workflows.py
16 passed in 0.19s

control-plane registry command
33 passed in 0.39s

scripts/head_truth_gate.py run --suite light --keep-going
fallback-ratchet: PASS, 21 passed in 184.92s
control-plane-ratchets: PASS, 33 passed in 0.32s
exit 0

Ruby YAML safe-load for all three touched workflow files
OK

git diff --check
exit 0 after correcting the generated trailing whitespace
```

## Claim boundary

This proves workflow structure, centralized command ownership, strict selected
gate semantics, and the complete local light invocation. It does not prove that
GitHub has run these uncommitted workflow files. `M0-GITHUB-STATUS-CHECKS`
remains open until a clean SHA has a visible light status and an uploaded heavy
truth artifact with `claimable_commit=true`.

## GitHub readback

After the shared worktree was externally amended to
`81a252d882d84b601368b154d8cf34d3b40f8056`, read-only GitHub queries against
both configured repository identities returned no status contexts and no
workflow runs for that SHA:

```text
commit status state: pending
statuses: []
workflow runs: []
```

This confirms the remaining boundary instead of treating workflow source files
as already-published status evidence.

## Blocker audit

The same external boundary was confirmed on three consecutive goal turns:

1. `M0-GITHUB-STATUS-CHECKS` remained the only dependency-ready M0 card.
2. HEAD remained `81a252d882d84b601368b154d8cf34d3b40f8056` with the new
   workflow files uncommitted.
3. GitHub continued to return `statuses: []` and `workflow runs: []`.
4. The canonical protocol continued to prohibit commits unless the user asks.

No other M0 work can close `M0-EXIT`, and milestone selection prevents skipping
to M1. The task is therefore `BLOCKED`, not `DONE_WEAK`. It is unblocked only by
explicit authorization to commit the current M0 change set and a named push
remote/branch.
