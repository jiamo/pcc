# 2026-07-16 V-P1-VAL five-GC timeout blocker evidence

Tasks: `V-P1-VAL`, `M3-FIVE-GC-MATRIX-PERF`

## What is green

All three finite value-model slices are focused-green:

- VP-S1 raw range-lane re-entry;
- VP-S2 selected 1–7 field aggregate ABI and recursive pointer slot schema;
- VP-S3 direct identity-surface diagnostics and object-projection boundary.

The closing focused valueclass gate passed:

```text
110 passed in 22.10s
```

## What is not green

The required aggregate five-GC bootstrap has no final pytest summary.

The first run used the stale 900-second `V-P1-VAL` task envelope.  It emitted
one dot (GC0), reached GC1 stage3, then exited 124.  The process group was
reaped and no child survived.

The corrected run used the 1800-second envelope already registered by the
head-truth gate.  It emitted three dots (complete GC0, GC1, and GC2), reached
GC3 stage2, then exited 124 before GC4.  Again there was no pytest summary and
no surviving pytest/bootstrap/pcc child.

Completed corrected-run profiles:

```text
GC0: stage2 169.982s + stage3 175.656s = 345.638s
GC1: stage2 288.726s + stage3 307.811s = 596.537s
GC2: stage2 339.558s + stage3 303.819s = 643.377s
combined before GC3: 1585.552s
```

The five files are deliberately serialized by
`tests/python/gc/conftest.py` under one `xdist_group`; this is not pytest
collection delay or git/staging work.  Focused scheduling tests passed:

```text
2 passed in 0.60s
```

## Claim boundary

This evidence does not promote `V-P1-VAL`.  It proves the focused valueclass
behavior and three complete backend fixed points, but an interrupted run with
three dots is not a five-GC matrix result.

The new `M3-FIVE-GC-MATRIX-PERF` row owns the remaining finite blocker: reduce
first-clean matrix cost or add a strict content-addressed resume manifest, then
produce one final five-test summary without weakening no-libpython,
pcc2/pcc3-identity, freshness, or per-backend runtime execution.

