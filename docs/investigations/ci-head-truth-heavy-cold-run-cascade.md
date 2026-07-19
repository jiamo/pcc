# Investigation: heavy HEAD truth cold run cascades after runtime setup and xdist oversubscription

## Status

active — local repair confirmed; clean published-source rerun pending

## Problem Description

Clean GitHub heavy run `29094810339` for published source `27eabf2a` executes
and uploads all keep-going evidence, but the manifest is not claimable.  The
first failure is a 300-second fallback-ratchet timeout.  Later gates expose a
failed lazy pcc-Python runtime archive build, an LLVM bootstrap timeout, and
five supposedly grouped self-GC bootstrap tests running concurrently on five
xdist workers.  The failures must remain separated until cold runtime setup and
bootstrap scheduling are deterministic.

This follows the resolved setup-contract investigations
`ci-head-truth-locked-sync-missing-uv-lock.md`,
`ci-head-truth-editable-build-hook-bootstrap.md`, and
`ci-head-truth-keep-going-timeout-envelope.md`.  Git operations are outside the
current execution boundary and are not a blocker.

## Repro

Inspect the uploaded heavy artifact from run `29094810339`:

```bash
gtimeout 60s gh run download 29094810339 \
  --name head-truth-heavy-27eabf2ab716f11030ec2d206de8f28c965bab76 \
  --dir /tmp/pcc-heavy-29094810339
gtimeout 20s sed -n '1,280p' /tmp/pcc-heavy-29094810339/manifest.json
gtimeout 20s rg -n \
  'TIMEOUT|failed to build py_runtime|bootstrap_gc_parallel_slots|Segmentation fault' \
  /tmp/pcc-heavy-29094810339/logs/*.log
```

Expected reproducing markers:

```text
fallback-ratchet: TIMEOUT after 300.0s
gc-production-contract: 15 passed, 125 errors
warning: failed to build py_runtime (... exit status 2)
llvm-bootstrap: pcc1 present; pcc2 and pcc3 missing after 900.0s
bootstrap_gc_parallel_slots = 5
GC0/1/2 stage2 return code 124
GC3/4 parallel frontend workers: Segmentation fault: 11
manifest complete=false, claimable_commit=false
```

## Test [CONFIRMED]

GitHub run `29094810339` is the clean-run integration reproducer.  The uploaded
artifact `8229140020` contains the manifest and all five logs with the markers
above.  The workflow's final clean-commit enforcement step failed as designed.

Focused regressions still need to be added before production changes:

- the heavy registry must contain an explicit, required, logged runtime archive
  preflight before any gate can trigger the lazy build;
- collection/scheduling evidence must prove the five full bootstrap node IDs
  carry the same xdist group before `loadgroup` assigns workers.

## Proposals

- No.1 Register an explicit runtime-archive preflight [CONFIRMED]
- No.2 Make the five-GC xdist group visible before xdist rewrites node IDs [CONFIRMED]
- No.3 Recalibrate measured timeout and worker budgets after removing contention [CONFIRMED]
- No.4 Preserve TIMEOUT when artifact inspection adds failure detail [CONFIRMED]

## No.1 Register an explicit runtime-archive preflight

### Code Change

Add a required heavy gate before the fallback/GC/bootstrap gates that builds
`libpy_runtime_pcc_py.a` with the same pcc and Python executables used by the
frontend.  Capture its full stdout/stderr in its own manifest log.  A failure
must stop claimability while keep-going still records later independent gates.
Update the registry-derived workflow timeout envelope from the registered gate
budget rather than hiding the work inside the first pytest that happens to need
the archive.

### CONFIRMED

The exact make command completed successfully in an isolated Python 3.13
locked candidate and produced `libpy_runtime_pcc_py.a`.  The gate is registered
first in the all-suite order, is a required manifest gate, and has its own
900-second process-group timeout and log.  The registry-derived heavy workflow
envelope is now 65 minutes for the truth step and 85 minutes for the job.

```text
gtimeout 900s env -u LC_ALL make -B -C pcc/py_runtime \
  libpy_runtime_pcc_py.a \
  PCC=/tmp/pcc-runtime-preflight-py313.JGTjfm/.venv/bin/pcc \
  PYTHON=/tmp/pcc-runtime-preflight-py313.JGTjfm/.venv/bin/python3
exit 0; archive atomically published

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_head_truth_manifest.py tests/test_head_truth_workflows.py
14 passed in 0.23s
```

This confirms the explicit setup/diagnostic contract.  It does not yet prove
the hosted runner's cold archive build or any later semantic gate.

## No.2 Make the five-GC xdist group visible before xdist rewrites node IDs

### Code Change

Change the GC collection hook or static module markers so xdist appends the
same `@gc_full_bootstrap` suffix to all five node IDs before loadgroup
scheduling.  Add a focused test that observes the grouped node IDs or worker
assignment; source inspection of the conftest alone is insufficient.

### CONFIRMED

The regression invokes a nested real xdist session with two workers, loads the
repository GC conftest, and records the worker chosen for five lightweight test
nodes matching `full_three_stage_bootstrap`.  Before the change, the probe
failed with workers alternating across `gw0` and `gw1`.  Marking the conftest's
collection hook `tryfirst=True` makes it add `xdist_group` before xdist appends
the loadgroup suffix; all five then execute on one worker.

```text
before: FAILED; workers={'gw0', 'gw1'}

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_gc_bootstrap_xdist_group.py
1 passed in 0.57s

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_head_truth_manifest.py tests/test_head_truth_workflows.py
14 passed in 0.07s
```

The new scheduling regression is also part of the registered control-plane
ratchet.  This proves loadgroup assignment, not the five expensive bootstrap
results themselves.

## No.3 Recalibrate measured timeout and worker budgets after removing contention

### Code Change

Use the first clean run after No.1 and No.2 to set per-gate timeouts and the
heavy workflow envelope.  Increase a timeout only for a measured isolated
stage; do not mask runtime build failure or five-way CPU oversubscription with
a blanket job timeout increase.

### CONFIRMED

The first post-grouping complete run proved the first five gates and completed
four full GC fixed points, but the 900-second aggregate five-GC timeout killed
GC4 while it was producing `pcc3`.  Profiles showed all four prior backends had
complete stage2/stage3 results and GC4 had a complete stage2.  The loadgroup
suffix was then made part of the resource-plan calculation: grouped items mean
one real parallel slot, so the sole worker receives the available frontend and
self-backend CPU budget instead of a fictitious five-slot partition.

The aggregate five-GC timeout is now 1800 seconds.  The observed hosted
fallback boundary (`300.061s`) and a later local pass at `261.14s` also proved
the previous 300-second fallback budget too tight, so it is 420 seconds.  The
mechanically checked workflow envelopes are now 12/25 minutes for the light
truth step/job and 80/100 minutes for the heavy truth step/job.  LLVM remains
at 900 seconds because clean local runs pass in 319–396 seconds.

```text
focused five-GC gate after worker-budget fix
5 passed in 587.71s

final current-source all-suite
runtime-archive-preflight: PASS, 26.339s
fallback-ratchet: PASS, 21 passed in 177.20s
control-plane-ratchets: PASS, 42 passed in 0.75s
gc-production-contract: PASS, 140 passed in 27.20s
llvm-bootstrap: PASS, 319.463s, no libpython, pcc2 == pcc3
self-five-gc-bootstrap: PASS, 5 passed in 520.45s
manifest complete=true
```

The local manifest is deliberately not a release claim because its source
identity reports a dirty working tree.  A clean published-source rerun remains
the external gate.

## No.4 Preserve TIMEOUT when artifact inspection adds failure detail

### Code Change

Keep a process return code 124 classified as `TIMEOUT` even when bootstrap
artifact inspection discovers missing later-stage files.  Preserve the artifact
observation as detail without replacing the primary process outcome with
`FAIL`.

### CONFIRMED

The first post-fix all-suite exposed that a five-GC process killed at 900
seconds was reported as `FAIL` solely because GC4 `pcc3` was missing.  A focused
regression reproduced this state and failed before the change (`FAIL !=
TIMEOUT`); it passes after guarding artifact-status override for timeouts.

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_head_truth_manifest.py::test_timeout_status_survives_bootstrap_artifact_inspection
1 passed in 0.18s
```
