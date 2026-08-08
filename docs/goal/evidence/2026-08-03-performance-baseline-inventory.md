# Performance baseline inventory

Date: 2026-08-03

## PERF-P1-REGALLOC

The human reviewer reported a current-source run of:

```text
scripts/bootstrap.sh --stage 2
elapsed: 434.3 seconds
```

This is a mode-limited local baseline for the existing AArch64 Darwin self
backend. The original run did not preserve a source fingerprint, profile JSON,
peak RSS, or static stack-slot count, so it does not support claims about those
metrics. The task may use 434.3 seconds only as its stage2 non-regression
anchor. Its required gate must record the exact current-source fixture and
before/after slot traffic before any register-allocation benefit is claimed.

`benchmarks/results/m3_value_array_c_like.json` is an older, dirty-worktree,
mode-labeled runtime/IR artifact for the pinned value-array workload. It proves
that the fixture and its runtime oracle exist; it does not contain the slot
load/store baseline and must not be presented as one.

## Other open performance rows

`PERF-P1-FRAME-POOL` has directly recorded GC3/GC4 stage2 measurements in
`docs/investigations/gc-frame-index-entry-pool-perf.md`.

`PERF-P1-COMPILER-DESIGN-REFERENCE-AUDIT` uses the structured task board as its
finite count baseline; it is an audit-completeness task, not a runtime speedup
claim.

Every other unfinished performance row lacks a traceable current-source
baseline for its claimed metric as of this inventory. Those rows remain
`TODO_NEEDS_DESIGN`; their next valid action is baseline capture, not optimizer
implementation and not invention of a percentage threshold.
