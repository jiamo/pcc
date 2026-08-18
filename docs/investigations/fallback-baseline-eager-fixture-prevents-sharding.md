# Investigation: eager fallback fixture prevents bounded phase validation

## Status

resolved

## Problem Description

Required fallback baseline verification cannot be sharded by selecting its
tests: both closure fixtures eagerly run standalone codegen, a full multi-file
compile and contextual codegen before yielding any result. The combined gate
and an OFF-only selection both hit the 120s watchdog without a final summary.
This is a validation-harness boundary; no compiler failure is inferred.

## Repro

The combined contextual/bootstrap/fallback run is retained in
`build/span-projection-restored-v76-pre-stage3.log`. The subsequent OFF-only
five-node run uses -x -n0 -vv -s --tb=short and PCC_TEST_LIVE_PROGRESS=1;
`build/span-projection-restored-v76-fallback-off.log` shows all 210 standalone
modules followed by `multi-file 210 modules` before exit 124. Both timed-out
runs lacked a pytest summary. Immediate process checks found no related
pytest/compiler children.

## Test [CONFIRMED]

The two watchdog failures are observed. Before changing the fixture, add a
cheap regression proving a selected standalone/count phase does not execute
unrequested multi-file or contextual work. Keep all original gate assertions
and the same mode, source-closure and count classification semantics.

## Proposals

- No.1 Expose independently evaluated and cached fallback phases [pending].
- No.2 Widen the unchanged aggregate command timeout [DENIED].

## No.1 Phase evaluation

### Code Change

Split the fixture's three computations so tests request only the phase they
assert. Preserve reuse within a command, source/mode identity, environment
restoration and errors. Selecting per-module assertions must not first compile
the complete module; selecting contextual assertions must not repeat standalone
codegen merely to obtain module names. Retain durable per-phase progress.

### Pending

Run the cheap regression red/green, then execute the original assertions in
bounded standalone, multi-file and contextual shards. This refactor may not
skip modules, weaken thresholds or substitute old artifacts for current source.

## No.2 Larger aggregate watchdog

### DENIED

The repository requires sharding after a suite cannot fit its watchdog. Tests
already have independent assertions, so forcing each selection to rerun every
phase is avoidable harness work. Fix that boundary first.

## Report

No.1 is CONFIRMED. Three lazy phase computations preserve the original node
IDs, assertions, classification and environment semantics. The red selection
test becomes 13 passing isolation/cache/error tests. Actual standalone,
multi-file and contextual shards finish under the original 120s envelope.
Fresh node inventory proves 37/37 fallback nodes covered by successful logs;
IR fallback adds 8 passing nodes. No interrupted aggregate run is counted.
See [the final coverage receipt](../goal/evidence/HARNESS-P1-FALLBACK-PHASE-SHARDS/001-phase-isolation-and-current-fallback-surfaces.md).

The harness exposed separate static-export and source-feature diagnostic
issues; their repairs/recapture are explicitly attributed in that receipt and
do not change the phase-isolation semantics. The original aggregate watchdog
was not widened. No commits were made by this task.
