# Investigation: HEAD truth workflows require an absent committed uv lockfile

## Status

active — local fix confirmed; successor clean GitHub run pending

## Problem Description

The push-triggered light truth workflow and the manually dispatched heavy
truth workflow both fail before running any PCC gate.  Their dependency step
uses `uv sync --dev --locked`, but the clean GitHub checkout has no `uv.lock`:
the repository ignores the root lockfile and commit
`c4969cc8109006e8541d26e33a2dc56aa43f3c1c` does not contain it.  The local
workflow contract tests missed this because an ignored, valid `uv.lock` exists
in the developer worktree.

## Repro

Published failures:

- light push run `29086837748`
- heavy manual run `29086968417`

Both report:

```text
error: Unable to find lockfile at `uv.lock`, but `--locked` was provided.
```

The source boundary is reproducible without rerunning CI:

```bash
gtimeout 20s git show HEAD:uv.lock
# expected: exit 128, path exists on disk but not in HEAD

gtimeout 20s git check-ignore -v uv.lock
# expected: .gitignore:76:uv.lock uv.lock
```

## Test [CONFIRMED]

The focused workflow-contract regression requires both locked workflows to
ship a root lockfile and prevents the root lockfile from being ignored.  It was
observed failing before Proposal No.1:

```text
gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_head_truth_workflows.py::test_locked_truth_workflows_ship_the_root_lockfile
FAILED: assert "uv.lock" not in ignored_root_entries
1 failed in 0.19s
```

The regression was then strengthened to require `git ls-files` tracking, not
only a locally present unignored file.  That assertion was separately observed
red while the lockfile was `??` and green after staging the root lockfile:

```text
FAILED: assert _is_git_tracked(lockfile.relative_to(ROOT))
1 failed in 0.06s

git add -- uv.lock
1 passed in 0.06s
```

## Proposals

- No.1 Track the valid root lockfile used by locked CI syncs [CONFIRMED]

## No.1 Track the valid root lockfile used by locked CI syncs

### Code Change

Keep `--locked` in both truth workflows, remove the root `uv.lock` ignore rule,
ship the already-valid generated lockfile, and add the focused source-contract
regression.  This preserves deterministic dependency resolution instead of
weakening CI to an unlocked install.

### CONFIRMED

The root lockfile is valid (`uv lock --check` and local `uv sync --dev
--locked` both pass), only the root lock is unignored, the root lock is staged
in Git's index, and the focused workflow/manifest gate passes.  An isolated
candidate Git archive also found and resolved the lockfile before exposing the
separate editable-build-hook failure recorded in
`ci-head-truth-editable-build-hook-bootstrap.md`.

A successor clean-checkout GitHub rerun remains the publication gate; it is not
part of the already-confirmed missing-lockfile diagnosis.
