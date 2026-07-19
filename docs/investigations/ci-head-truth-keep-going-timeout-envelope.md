# Investigation: HEAD truth workflow timeouts cannot cover keep-going gates

## Status

active — local fix confirmed; successor clean GitHub run pending

## Problem Description

The truth workflows invoke the centralized gate runner with `--keep-going`, but
their step timeout envelopes are shorter than the sum of the selected registry
gate timeouts.  The light step allows seven minutes for eight minutes of
registered gates.  The heavy `--suite all` step allows thirty minutes for
forty-five minutes of registered gates.  A slow or timed-out early gate can
therefore cause GitHub to kill the runner before later selected gates execute
or the final manifest is written, contradicting the workflow contract.

This is separate from the missing committed `uv.lock` failure recorded in
`ci-head-truth-locked-sync-missing-uv-lock.md`.

## Repro

```bash
gtimeout 20s rg -n 'timeout_seconds|timeout-minutes|keep-going' \
  scripts/head_truth_manifest.py .github/workflows/head-truth-*.yml
```

Observed registry sums and workflow envelopes:

```text
light registry: 300 + 180 = 480 seconds; workflow step: 7 minutes
all registry: 300 + 180 + 420 + 900 + 900 = 2700 seconds; workflow step: 30 minutes
```

## Test [CONFIRMED]

The workflow-contract test derives each selected suite's worst-case gate
duration from `gate_specs()`.  It requires one minute of runner overhead and a
five-minute job margin beyond all explicit step timeouts.  It was observed
failing before Proposal No.1:

```text
gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_head_truth_workflows.py::test_keep_going_workflow_timeouts_cover_every_selected_gate
FAILED: assert 420 >= (480 + 60)
1 failed in 0.20s
```

## Proposals

- No.1 Widen and mechanically gate the workflow timeout envelopes [CONFIRMED]

## No.1 Widen and mechanically gate the workflow timeout envelopes

### Code Change

Increase the light truth step/job timeout to ten/twenty-five minutes and the heavy
truth step/job timeout to fifty/seventy minutes.  Add a registry-derived test so
future gate timeout changes cannot silently exceed either workflow envelope.

### CONFIRMED

The registry-derived regression passes with 10/25-minute light step/job limits
and 50/70-minute heavy limits.  The focused manifest/workflow set passes (`13
passed in 0.21s`), and the exact local light registry completes both selected
gates under keep-going (`21 passed in 182.26s`; `37 passed in 0.38s`).

A successor clean-checkout GitHub rerun remains the publication gate.
