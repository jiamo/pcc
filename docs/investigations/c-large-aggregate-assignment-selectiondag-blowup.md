# Investigation: large C aggregate assignment causes SelectionDAG blow-up

## Status
resolved

## Problem Description

After the `stdatomic.h` parse failure was fixed, pcc needed more than 240
seconds to emit one object for `pcc/py_runtime/src/py_re_engine.c`.  This was a
compiler design problem, not an acceptable slow-file exemption: a source-level
copy of `ReFrag`, which contains `int32_t patch[4096]`, was materialized as a
huge aggregate SSA load/store.

## Repro

```bash
gtimeout 240s env -u LC_ALL .venv/bin/pcc --no-cache \
  --cpp-arg=-Ipcc/py_runtime/include --cpp-arg=-Ipcc/py_runtime/src \
  --emit-obj /tmp/pcc_py_re_engine.o pcc/py_runtime/src/py_re_engine.c
```

Pre-fix: exceeded 240 seconds.  A 10-second macOS `sample` captured 8,270/8,270
main-thread samples inside LLVM AArch64 SelectionDAG emission, predominantly
`DAGCombiner::visitMERGE_VALUES`; physical footprint peaked near 2 GB.
Emit-LLVM showed `re_parse_rep` at 67,965 lines and the adjacent recursive
parser functions at roughly 12.5k lines each.

## Test [CONFIRMED]

`test_large_struct_assignment_uses_bounded_aggregate_copy_ir` reproduces the
generic shape with a 4,096-element struct member.  Before the fix it had no
`llvm.memmove` and failed the bounded-IR assertion.  After the fix it executes
the copied boundary values correctly and checks that aggregate extraction does
not explode.

## Proposals

- No.1 Lower large direct addressable aggregate assignment with `llvm.memmove` [CONFIRMED]

## No.1 Lower large direct addressable aggregate assignment with `llvm.memmove`

### Code Change

For direct aggregate objects and `*pointer_name` on both sides of `=`, use one
`llvm.memmove` when the aggregate is at least 128 bytes.  The narrow syntactic
gate avoids evaluating side-effecting expressions twice; other shapes retain
the existing lowering.  A load of the destination preserves C assignment-value
semantics when the expression is used and is dead-code-eliminated for ordinary
assignment statements.  `memmove` preserves exact representation and overlap.

The runtime emit test no longer grants `py_re_engine.c` a 1,200-second special
case; it uses the same 120-second limit as every runtime source.

### CONFIRMED

- Direct `py_re_engine.c --emit-obj`: 8.19 seconds after the fix, versus a
  pre-fix 240-second timeout (the historical test exemption described ~300s).
- Largest post-fix parser function: 3,365 IR lines; the 67,965-line function is
  gone.
- Focused aggregate/stdatomic/atomic/runtime-emit gate: 4 passed in 6.71s.

## Report

The optimization is in the generic C aggregate assignment lowering and does
not special-case regex or reduce test coverage.  It removes the compiler IR
shape that made LLVM SelectionDAG superlinear, while retaining a slow fallback
for non-direct or small aggregate assignment forms.
