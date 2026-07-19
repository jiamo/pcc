# CI heavy cold-run local repair evidence

Date: 2026-07-10

Milestone: `M0`

Tasks: `M0-CI-WORKFLOW-CONTRACT`, `M0-GITHUB-STATUS-CHECKS`

## Repair

- Register `runtime-archive-preflight` as the first all-suite gate and as a
  required manifest claim.  It runs the exact `make -B` archive build with the
  active pcc/Python executables and captures full output in its own log.
- Run the GC full-bootstrap collection hook before xdist's node-id rewrite.
  A real nested-xdist regression proves all five matching nodes use one
  `loadgroup` worker.
- Treat grouped bootstrap nodes as one real resource slot so the sole worker
  receives the available frontend/self-backend job budget.
- Preserve `TIMEOUT` as the primary gate outcome when later artifact
  inspection also sees missing stage files.
- Calibrate only measured capacity limits: fallback 300s -> 420s and aggregate
  five-GC 900s -> 1800s.  LLVM remains 900s.
- Keep workflow envelopes derived from the registry: light truth/job 12/25
  minutes; heavy truth/job 80/100 minutes.

## Red-green regressions

```text
runtime preflight registry
before: FAILED, gate absent
after: included in focused workflow/manifest pass

real xdist worker probe
before: FAILED, workers={'gw0', 'gw1'}
after: 1 passed in 0.57s, one worker

grouped resource slot
before: FAILED, 5 != 1
after: grouped slot and legacy ungrouped count, 2 passed

timeout preservation
before: FAILED, FAIL != TIMEOUT
after: 1 passed in 0.18s

final focused contract
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_head_truth_manifest.py tests/test_head_truth_workflows.py \
  tests/test_gc_bootstrap_xdist_group.py \
  tests/python/test_pcc_bootstrap_full.py::test_bootstrap_gc_parallel_slots_count_full_gc_files \
  tests/python/test_pcc_bootstrap_full.py::test_bootstrap_gc_parallel_slots_grouped_files_use_one_slot
19 passed in 0.69s
```

## Runtime archive cold proof

An isolated source candidate with Python 3.13 and `uv sync --dev --locked`
successfully executed the exact registered make command and atomically
published `libpy_runtime_pcc_py.a`.  The final current-source all-suite
preflight independently passed in 26.339 seconds.

## Exact current-source light registry

```text
gtimeout 720s env -u LC_ALL uv run python scripts/head_truth_gate.py run \
  --suite light \
  --output build/head-truth/manifest-light.json \
  --artifacts-root build/head-truth/logs-light \
  --keep-going

fallback-ratchet: PASS, 21 passed in 173.70s
control-plane-ratchets: PASS, 42 passed in 0.73s
manifest validation: OK
```

## Exact current-source complete matrix

```text
gtimeout 4900s env -u LC_ALL uv run python scripts/head_truth_gate.py run \
  --suite all \
  --output build/head-truth/manifest.json \
  --artifacts-root build/head-truth/logs \
  --keep-going

runtime-archive-preflight: PASS, 26.339s
fallback-ratchet: PASS, 21 passed in 177.20s
control-plane-ratchets: PASS, 42 passed in 0.75s
gc-production-contract: PASS, 140 passed in 27.20s
llvm-bootstrap: PASS, 319.463s
  links_libpython=false
  pcc2_pcc3_equal=true
self-five-gc-bootstrap: PASS, 5 passed in 520.45s
  GC0..4 links_libpython=false
  GC0..4 pcc2_pcc3_equal=true
manifest complete=true

gtimeout 30s env -u LC_ALL uv run python scripts/head_truth_gate.py validate \
  build/head-truth/manifest.json --require-complete
OK
```

No `pytest`, `bootstrap.sh`, `pcc1`, `pcc2`, or `pcc3` child remained after
the long gates.

## Claim boundary

This proves the current working-tree implementation and the complete local
light/heavy semantics.  It does not prove a clean published commit:
`source.worktree_dirty=true`, so the local manifest correctly has
`claimable_commit=false` even though `complete=true`.

Published source `27eabf2ab716f11030ec2d206de8f28c965bab76` still has the
older failed heavy run `29094810339` and artifact `8229140020`.  Both M0 CI
cards therefore remain `TESTING` until a future clean published-source heavy
run uploads `complete=true`, `claimable_commit=true` evidence.  Git operations
are outside this task and are not a blocker; no commit or push was performed.

## Post-proof portability refinement

The runtime preflight now records checkout-relative executables
(`PCC=../../.venv/bin/pcc`, `PYTHON=../../.venv/bin/python3`) instead of a
developer-specific absolute path.  The exact relative make command completed
successfully.  This changes CI command portability, not pcc compiler/runtime
behavior, so the interrupted follow-up matrix was not restarted.  The checked
snapshot records preflight, fallback, control plane, and GC production as
`PASS`, with LLVM and self-five-GC explicitly `NOT_RUN`; the earlier complete
fixed-point proof above remains the heavy semantic evidence.

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_goal_startup_docs.py tests/test_head_truth_manifest.py \
  tests/test_head_truth_workflows.py tests/test_gc_bootstrap_xdist_group.py \
  tests/python/test_pcc_bootstrap_full.py::test_bootstrap_gc_parallel_slots_grouped_files_use_one_slot
23 passed in 0.65s

head_truth_gate.py validate docs/goal/head-truth-manifest.json
OK
```
