# Lower-expect independent semantic oracle

Date: 2026-07-17

Task: `AUD-P1-IR-PASS-ORACLE-STRENGTH`

## Inventory and selected family

- The current C IR-pass surface contains 67 `test_ir_passes_*.py` files.
- The principal remaining categories are scalar/CFG folds, loop/vector
  transforms, memory/SSA transforms, and interprocedural/global transforms.
  They are not claimed complete by this slice.
- The finite selected family is `lower-expect`. Its earlier coverage checked
  rewritten text and LLVM `opt` shape parity. Those are useful implementation
  checks, but they did not execute the before/pcc/upstream programs.

## Strong oracle added

- Mode label: host llvmlite-MCJIT behavior plus independent Homebrew LLVM
  `opt -passes=lower-expect`; not self backend, pcc1, or bootstrap evidence.
- One `llvm.expect.i32` function is executed in original, pcc-lowered, and
  upstream-LLVM-lowered forms for ten signed i32 boundary inputs.
- All three result vectors match the independent arithmetic expectation.
- An AST source guard requires the selected claim test to call the pcc
  transform, upstream `opt`, and MCJIT executor, and forbids IR-substring
  membership assertions from becoming the selected semantic claim.
- Existing structural and upstream shape tests remain as secondary diagnostics.

## Gate

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_ir_passes_lower_expect_semantic_oracle.py \
  tests/c/test_ir_passes_lower_expect_real.py -rs
```

Result: `15 passed in 0.73s` (independent `opt` present; no skips).

## Remaining schedulable families

The board now carries separate follow-up rows for instsimplify, loop-rotate,
mem2reg, and inline as one finite representative from each inventoried
category. No unselected structural test is promoted to semantic proof by this
closure.

## Claim boundary

This proves semantic preservation and upstream agreement only for the selected
`lower-expect` i32 family in host LLVM mode. It does not prove every expect
intrinsic type, metadata/probability preservation, all 67 pass-test files, the
self backend, pcc1, bootstrap, or optimized-program performance.
