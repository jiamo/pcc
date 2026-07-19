# CI clean-checkout contract repair evidence

Date: 2026-07-10

Tasks: `M0-CI-WORKFLOW-CONTRACT`, `M0-GITHUB-STATUS-CHECKS`

Source identity: base commit
`c4969cc8109006e8541d26e33a2dc56aa43f3c1c`, dirty-worktree light-manifest
fingerprint `51db272c03d58322ce0d787c9c8989d7cf12d85cbe29da2c59b313f034c6b556`.
The converged repair is now also present in published source
`27eabf2ab716f11030ec2d206de8f28c965bab76` on `shenming/master`.

## Observed GitHub boundary

- Push-triggered light run `29086837748` and manually dispatched heavy run
  `29086968417` both failed before any truth gate.
- The first failure was `uv sync --dev --locked` with no committed `uv.lock`.
- After putting the lockfile into an isolated candidate Git archive, dependency
  resolution succeeded and exposed the separate editable-build-hook failure:
  Hatch attempted the release/native self-compile inside its isolated build
  environment before the explicit truth gates ran.
- Static audit also proved the keep-going timeout envelopes were too short:
  light allowed 420 seconds for 480 seconds of registered gate timeouts; heavy
  allowed 1800 seconds for 2700 seconds.

## Change

- The valid root `uv.lock` is now source-visible through a root-only
  `.gitignore` exception and staged in Git's index; nested project lockfiles
  remain ignored.  The regression requires `git ls-files` tracking so an
  omitted untracked file cannot make local workflow tests falsely green.
- Both dependency-install steps set the documented development-only
  `PCC_BUILD_SKIP=1`.  The environment is step-scoped and does not weaken the
  explicit truth-gate steps.
- Light truth step/job limits are 10/25 minutes; heavy limits are 50/70 minutes.
- Workflow regressions derive timeout requirements from `gate_specs()`, lock
  the root/nested lockfile boundary, and lock build-skip scoping.
- Three separate investigations preserve the stacked failure chains:
  `ci-head-truth-locked-sync-missing-uv-lock.md`,
  `ci-head-truth-editable-build-hook-bootstrap.md`, and
  `ci-head-truth-keep-going-timeout-envelope.md`.

## Gates

```text
focused manifest/workflow contract
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_head_truth_manifest.py tests/test_head_truth_workflows.py
13 passed in 0.21s

tracked-lock omission guard
before staging: FAILED _is_git_tracked(uv.lock), 1 failed in 0.06s
after `git add -- uv.lock`: 1 passed in 0.06s
git diff --cached --check -- uv.lock: exit 0

lock validity
gtimeout 30s env -u LC_ALL uv lock --check
Resolved 16 packages

isolated candidate checkout, Python 3.13, exact install environment
gtimeout 180s env -u LC_ALL PCC_BUILD_SKIP=1 \
  UV_PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 uv sync --dev --locked
Resolved 16 packages; built python-cc; installed 15 packages; exit 0

independent adversarial fresh-clone audit
step-scoped PCC_BUILD_SKIP=1 locked sync: exit 0
subsequent truth run with PCC_BUILD_SKIP unset:
fallback 21 passed; control plane 37 passed; exit 0; manifest validates

exact local light registry
gtimeout 600s env -u LC_ALL uv run python scripts/head_truth_gate.py run \
  --suite light \
  --output build/head-truth/manifest-light.json \
  --artifacts-root build/head-truth/logs-light \
  --keep-going
fallback-ratchet: PASS, 21 passed in 182.26s
control-plane-ratchets: PASS, 37 passed in 0.38s
exit 0

manifest validation
gtimeout 30s env -u LC_ALL uv run python scripts/head_truth_gate.py validate \
  build/head-truth/manifest-light.json
OK

Ruby syntax parse of head-truth-light.yml, head-truth-heavy.yml, workflow.yml
exit 0

git diff --check on touched workflow/control-plane/investigation/task files
exit 0

goal_state.py validate
OK: 76 tasks validated
```

## Claim boundary

This proves the repaired dependency-install contract, timeout accounting, and
the complete local light suite.  Published-source light run `29094565455`
subsequently passed for `27eabf2a`.  Heavy run `29094810339` did not pass; its
separate failure record is
`docs/goal/evidence/2026-07-10-ci-heavy-cold-run-failure.md`.  A successful
heavy GitHub result, uploaded clean-commit truth artifact, and
`claimable_commit=true` remain required before either M0 CI card can become
`DONE_STRONG` and before `M0-EXIT` may activate M1.

## Publication resumed

Git operations are explicitly outside the active blocker model.  Published
source `27eabf2a` contains the repair, and GitHub created push-triggered light
run `29094565455` at `2026-07-10T13:01:46Z`.  It completed successfully in
3m38s: fallback `21 passed in 183.86s`, control plane `37 passed in 0.34s`,
artifact `8228262758` uploaded, and final manifest enforcement passed.  Heavy
run `29094810339` completed with a non-claimable manifest, moving
`M0-CI-WORKFLOW-CONTRACT` back to `IN_PROGRESS`.  Git operations remain outside
the blocker model; the remaining work is the heavy cold-run implementation and
execution boundary.
