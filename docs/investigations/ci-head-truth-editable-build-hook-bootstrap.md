# Investigation: HEAD truth dependency sync invokes the release build hook

## Status

active — local fix confirmed; successor clean GitHub run pending

## Problem Description

After supplying the missing committed `uv.lock`, an isolated candidate
checkout reaches dependency resolution but `uv sync --dev --locked` then
attempts an editable build of PCC.  The Hatch hook self-compiles the runtime
archive and native `pcc1` inside Hatch's isolated build environment, where
`llvmlite` is unavailable; the fallback self-compile also fails to link.  The
truth workflow therefore still cannot reach its explicit registry gates.

This is the next stacked boundary after
`ci-head-truth-locked-sync-missing-uv-lock.md`.  The same build-isolation
mechanism and its intended development-harness solution were previously
confirmed in `linux-x86-64-docker-harness-rot.md`: set `PCC_BUILD_SKIP=1` only
for the editable dependency-install step, then let the explicit test/bootstrap
gates own compiler proof.

## Repro

Construct an isolated candidate Git tree containing the root lockfile, then
run:

```bash
gtimeout 180s env -u LC_ALL uv sync --dev --locked
```

Dependency resolution succeeds, followed by:

```text
ModuleNotFoundError: No module named 'llvmlite'
RuntimeError: pcc self-compile failed under both self and llvm backends.
```

## Test [CONFIRMED]

The focused workflow-contract regression requires the documented
development-only `PCC_BUILD_SKIP=1` environment on each dependency install
step, while leaving the truth-gate steps themselves outside that step-local
environment.  It was observed failing before Proposal No.1:

```text
gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_head_truth_workflows.py::test_dependency_sync_skips_the_release_build_hook
FAILED: assert 'PCC_BUILD_SKIP: "1"' in install_step
1 failed in 0.20s
```

## Proposals

- No.1 Scope PCC_BUILD_SKIP to dependency installation [CONFIRMED]

## No.1 Scope PCC_BUILD_SKIP to dependency installation

### Code Change

Set `PCC_BUILD_SKIP: "1"` on the light and heavy `Install dependencies` steps
only.  Keep the explicit registry gates unchanged: they continue to exercise
the fallback ratchets, GC contract, LLVM bootstrap, and self/five-GC bootstrap
instead of conflating dependency installation with release-wheel construction.

### CONFIRMED

With `PCC_BUILD_SKIP=1` scoped to dependency installation, an isolated
candidate checkout under Python 3.13 resolved 16 packages, built the editable
`python-cc` package, installed 15 packages, and exited zero.  The full focused
manifest/workflow test set then passed (`13 passed in 0.21s`).  The explicit
truth steps do not inherit the skip variable.

A successor clean-checkout GitHub rerun remains the publication gate.
