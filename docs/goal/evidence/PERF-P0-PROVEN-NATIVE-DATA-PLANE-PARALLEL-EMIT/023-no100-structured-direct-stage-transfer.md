# No.100 structured direct plane Stage1/Stage2 transfer

The structured llvm_capi-to-indexed-kernel path cleared its registered host
prefilter with exact assembly: 21.11s/325.77B/834MB for text versus
16.35s/241.18B/762MB for direct, or 1.291x wall, 0.740x instructions and
0.914x RSS.

Source-frozen GC0, no-libpython, self-backend receipts then established:

- Stage1: 199.40s wall, 808.02s CPU, 92.267B instructions, 1.656GB peak
  footprint; the function-bearing compile/run canary printed `42` and linkage
  was libSystem-only.
- Stage2: 482.181s compile, 15.016s publication barrier, 497.213s total,
  22.643GB peak process-tree RSS; linkage was libSystem-only.
- Stage2/Stage1 is still 2.418x on compile wall (2.494x including the barrier),
  so this evidence does not satisfy the task's terminal performance gate.

The representative pcc1 direct/text pair emitted the same
`8a1dd24944b543356315948afc760f6b7c4d591aebb153b7740bcd6ebcc3b833`
assembly.  Direct reduced 70.50s to 68.98s, 973.64B instructions to 942.66B,
and 9.596GB footprint to 6.649GB.  The remaining pcc1 caller profile points to
dynamic direct-builder method adapters, not text parsing or the linker.

Primary artifacts:

- `build/no100-direct-stage1-candidate-v9/`
- `build/no100-direct-stage2-candidate-v9/`
- `build/no100-v9-pcc1-module1-direct/`
- `build/no100-v9-pcc1-module1-text/`
- `build/no100-v9-pcc1-module1-direct-profile.svg`

This proves a correct, materially smaller direct data plane and a valid faster
Stage2.  It does not prove Stage2 <= Stage1, pcc2/pcc3 fixed point, or GC1--4.
