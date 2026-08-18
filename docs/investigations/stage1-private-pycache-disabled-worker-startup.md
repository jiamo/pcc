# Investigation: private Stage1 environment disables every worker bytecode cache

## Status

resolved

## Problem Description

The source-frozen Stage1 harness gives every CPython process an isolated
`PYTHONPYCACHEPREFIX`, but also sets `PYTHONDONTWRITEBYTECODE=1`.  The prefix
already prevents source-tree and cross-arm cache pollution; the second setting
keeps the private cache permanently empty.  The Stage1 compiler launches 219
short-lived summary workers plus export/codegen/link helpers, so every process
reparses and recompiles the same pcc modules.

This is a measurement/build-environment defect, not a Python compiler semantic
change.  It primarily affects host-CPython Stage1 and must not be credited as a
pcc1 Stage2 optimization.

Predecessors:

- `pcc1-indexed-function-kernel-native-data-plane.md` records the accepted
  concurrency-two short-lived summary-worker architecture and its memory
  reason; this investigation does not change that concurrency.
- `pcc1-stage2-emit-throughput-and-memory.md` records that host and native cost
  models differ and require boundary-specific measurements.

## Repro

Using CPython 3.15.0rc1 and the frozen v19 source snapshot, alternating
`python -m pcc --help` starts measured:

```text
private prefix + DONTWRITEBYTECODE     0.562--0.582s, zero pyc files
private prefix, first writable start   0.615s, 175 pyc files produced
private prefix, later writable starts  0.105--0.107s
```

The retained one-module summary manifest reproduces the real worker boundary:

```text
private prefix + DONTWRITEBYTECODE     0.562--0.620s
private prefix, warm writable cache    0.106--0.112s
```

Both arms exit zero with empty stderr.  The private prefix is outside the
read-only source snapshot.

## Test [CONFIRMED]

The timing defect was observed on 2026-08-31 with the frozen v19 source and
real summary-worker manifest.  `tests/python/test_pcc_compile_ab_tool.py`
will require `_measurement_env` to discard an ambient
`PYTHONDONTWRITEBYTECODE` and retain a private writable
`PYTHONPYCACHEPREFIX`.

## Proposals

- No.1 Allow writes only to each arm's private pycache [CONFIRMED]

## No.1 Allow writes only to each arm's private pycache

### Code Change

Remove `PYTHONDONTWRITEBYTECODE` from `_measurement_env`; keep the per-arm
private prefix, private HOME/TMP/cache roots, fixed hash seed, frozen source
manifest, and cache-off compiler settings unchanged.  Do not prewarm outside
the timed command: the first import and bytecode writes remain part of Stage1,
while later workers reuse only artifacts created by that same measured arm.

Acceptance requires the focused harness tests, one source-frozen Stage1 with a
non-empty private pycache, exact runnable/libSystem-only pcc1, and a phase
receipt compared with v19.  Stage2, Stage3 and GC1--4 are not required for this
host-only harness correction.

### CONFIRMED

`_measurement_env` now removes inherited `PYTHONDONTWRITEBYTECODE` while
preserving its isolated per-arm prefix. The focused harness suite passes
48/48. Two complete adjacent alternating No.100-v13/current-source pairs each
produced 338 private `.pyc` files per Stage1 arm, runnable pcc1/pcc2 outputs and
libSystem-only linkage. The combined receipt explicitly labels tree CPU,
coordinator-only counters and wall's paired-observation role, and refuses an
automatic source verdict.

## Report

No.1 is the sole confirmed proposal. It removes a roughly 0.45s repeated
import penalty from hundreds of host workers without prewarming outside the
timed arm or writing into the source tree. The retained evidence is
`docs/goal/evidence/2026-08-31-bootstrap-measurement-contract.md`; current
Stage2 remains far slower than Stage1, so this harness fix is not claimed as a
compiler optimization or performance-goal completion.
