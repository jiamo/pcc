# Investigation: non-integration heavy xdist lanes oversubscribe compiler workloads

## Status

resolved

## Problem Description

The complete non-integration suite reported three timeout boundaries at once:

- `os_basics` and `path_basics` in the pcc-Python runtime oracle exceeded
  their 120-second per-compile watchdogs.
- the LLVM / GC backend 4 metadata slice exceeded its 300-second inner-pytest
  watchdog.

The affected files used distinct `xdist_group` names. Under the repository
default `-n 6 --dist=loadgroup`, the runtime oracle and each GC metadata group
were therefore independent work units. Keeping cases for one backend on one
worker did not reserve that worker or limit aggregate nested subprocess
concurrency; the scheduler could occupy all workers with compiler-heavy inner
workloads.

This investigation owns suite-level scheduling only. It does not claim a
runtime, GC4, `os.path`, or pcc-Python semantic defect.

## Repro

Focused isolation:

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  'tests/python/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_py_equivalence[os_basics]'
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  'tests/python/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_py_equivalence[path_basics]'
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  'tests/python/test_gc_backend_under_env.py::test_gc_backend_subset_under_frontend_backend[frontend=llvm-gc=4]'
```

Observed current-source results:

- `os_basics`: `1 passed in 1.86s`.
- `path_basics`: `1 passed in 1.36s`.
- LLVM / GC4: `1 passed in 35.01s`.

All are far below their unchanged internal watchdogs when xdist and competing
heavy groups are absent. The deterministic reduced scheduling failure is the
old marker map itself: it exposed one runtime-oracle group plus one group per
GC backend instead of an aggregate concurrency budget.

## Test [CONFIRMED]

The scheduler contract in
`tests/test_test_infrastructure_efficiency.py::test_gc_meta_matrix_retains_required_modes_without_accidental_duplicates`
requires the runtime oracle and GC metadata cases to expose exactly two heavy
work units. Before the scheduling change it failed:

```text
assert {'gc_meta_2', 'gc_meta_3', 'gc_meta_4'}
    == {'pcc_heavy_llvm', 'pcc_heavy_self'}
```

After the marker change, that contract and the default-xdist structural guard
pass: `2 passed in 0.12s`.

## Proposals

- No.1 Use two frontend-shaped heavy xdist lanes
  [CONFIRMED]

## No.1 Use two frontend-shaped heavy xdist lanes

### Code Change

Map every complete LLVM GC metadata slice to `pcc_heavy_llvm`. Map every
reduced self-backend GC metadata slice and the runtime oracle to
`pcc_heavy_self`. The two groups remain independently schedulable, but
`--dist=loadgroup` can no longer launch one nested pytest per GC backend plus a
separate runtime-oracle compiler workload.

The self lane owns fewer GC target nodes than the LLVM lane, so placing the
runtime oracle there balances the two scheduler work units. The 120, 240, and
300-second subprocess watchdogs remain unchanged.

### CONFIRMED

The focused scheduler contract is green. The affected files also pass together
through the repository's normal six-worker scheduler:

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q \
  tests/python/test_gc_backend_under_env.py \
  tests/python/test_runtime_oracle_diff.py
```

Result: `34 passed in 73.50s`. This includes every runtime-oracle case and every
LLVM/self GC metadata slice, with the original subprocess watchdogs unchanged.
The complete non-integration suite remains the final confirmation of the
reported broad-suite boundary.

## Report

Proposal No.1 is confirmed. The original three timeout nodes pass alone and
the complete affected runtime-oracle/GC-meta files pass together under the
normal six-worker scheduler. The generated goal-state drift was a separate
one-line generated-file repair.

The exact non-integration run still does not close the parent performance card:
it reached 50% and ended at the 900-second watchdog after spending roughly four
minutes provisioning stage1. It emitted two failure markers without tracebacks;
a bounded stop-on-first rerun reached 54% with no failure before its own
watchdog. Those marker-only failures are not attributed to this investigation.
The remaining stage1 reuse and complete-suite budget boundary is recorded on
`PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE`.
