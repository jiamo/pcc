# Investigation: self-backend freestanding external-resource harness stalls

## Status

resolved

## Problem Description

While moving `pcc_gc_external_resource` production ownership from C to strict
freestanding pcc-Python, the LLVM-emitted object matched the retained C oracle,
but the self-backend object did not finish the same combined five-backend,
callback-reentry, concurrent-release, and Metal-release harness.  The failure
was reduced to an AArch64 self-backend peephole misclassifying `stlr` as a
register definition and deleting a still-live value-producing move.

## Repro

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  'tests/python/test_freestanding_gc_external_resource.py::test_freestanding_external_resource_matches_c_oracle_under_gc0_to_gc4[self]'
```

Expected: the C oracle and self object both print the five `backend:0` lines,
`threaded:0`, and `metal-adapter:1`, then exit zero.  Before the fix, the C
oracle exited zero while the self executable reached the test's internal
60-second timeout.  After the fix the same command passes.

## Test [CONFIRMED]

`tests/python/test_freestanding_gc_external_resource.py` compiles the exact
same pcc-Python module through LLVM and self, runs both objects against the same
C harness, and compares both with `src/pcc_gc_external_resource.c`.  The
failure above was observed with `-n0`; no child process remained afterwards.
`tests/python/test_unsafe_atomic_global_store.py` is the minimized regression.

## Proposals

- No.1 Split the combined harness and identify the first self-only boundary [CONFIRMED]
- No.2 Repair the smallest proven self-backend lowering/runtime defect [CONFIRMED]

## No.1 Split the combined harness and identify the first self-only boundary

### Code Change

Add deterministic phase selection/progress flushes to the regression harness,
then run single-thread state transitions, callback reentry, thread contention,
and dynamic loading independently.  Do not change runtime semantics during
localization.

### CONFIRMED

The harness now accepts `single`, `threaded`, and `metal` phases and disables
stdio buffering before progress output.  The `single` phase stalled before its
first line.  Sampling placed the executable in
`pcc_gc_external_resource_register` while acquiring the lock for the second
registration: the first successful registration had failed to clear the lock.

## No.2 Repair the smallest proven self-backend lowering/runtime defect

### Code Change

Teach the AArch64 peephole liveness classifier that `stlr`, `stlrb`, and
`stlrh` consume rather than define their first register operand.  Add a direct
peephole regression and a compiled LLVM-versus-self test that atomically stores
constant zero to a global and reads it back.

### CONFIRMED

The unoptimized assembly correctly loaded zero into `w10` before
`stlr w10, [x9]`.  The optimized assembly had deleted that load because
`_line_defines_reg` treated `stlr` as defining `w10`; the later
`_fold_mov_store_source` pass then removed the move on which an earlier stack
forwarding pass depended.  Classifying the release-store opcodes with the
ordinary store family preserves the live value.

Observed gates:

```text
20 passed in 2.48s  # AArch64/Darwin and x86-64 atomic suites
3 passed in 2.59s   # single/threaded/metal self phase reductions
14 passed in 8.84s  # runtime-boundary + atomic + full external-resource suite
9 passed in 1.55s   # direct peephole, behavior, and encoder regressions
```

## Report

Both proposals were required.  The phase split proved the lock rather than
callback, thread, or dynamic-loader path was the first boundary; the minimized
atomic test then separated a general self-backend optimizer defect from the GC
port.  The correction is confined to store-opcode liveness classification and
does not weaken atomic ordering or GC semantics.  A separate production
archive harness failure is recorded in
`production-archive-external-resource-host-stdio-symbol-collision.md`.
