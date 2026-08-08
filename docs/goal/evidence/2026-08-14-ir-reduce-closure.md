# LLVM IR reducer closure evidence — 2026-08-14

Mode: explicit host tooling; the reducer is not default-on.

The fail-fast focused suite completed with 5 passed. It covers deterministic
function/block/instruction reduction, protected functions, rejection of a
non-interesting baseline, exact exit/stdout/stderr interestingness, independent
candidate materialization and timeout-as-uninteresting behavior.

The checked historical signed-division miscompile shape was measured directly:

```text
original_bytes=359
reduced_bytes=148
attempts=1
elapsed_s=0.000106
```

The reduced IR retains `sdiv i32 -2147483648, -1` and removes the dead helper
functions. Source-level and pcc frontend-IR reduction remain outside the finite
task claim rather than an open boundary.
