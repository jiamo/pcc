# PY-P0 exact-int branch representation — 2026-08-13

## Claim

An `int` local that can receive an exact/bignum value is planned once per
function and uses one owned, updateable object slot across branch fallthrough,
loops, copies, augmented assignment, comparison, and return. Proven raw range
induction remains in the scalar lane and is boxed at object-ABI escapes.

During execution verification, two adjacent self-backend defects were exposed
and fixed rather than hidden: AArch64 scalar floating load/store and direct
`fmov` immediate encoding, and exceptional cleanup of a LIFO-rooted owned user
call result. The latter now clears and leaves the temporary root on every error
edge before releasing the callee-owned result.

## Fail-fast gates

All commands used `-x`; the production runtime archive was supplied explicitly
for compiled runtime cases.

- `tests/python/test_py_typed_int_unboxed.py`: **45 passed**.
- typed-int/local-rebind cluster (`test_native_typed_int_overflow.py`,
  `test_py_local_rebind.py`, `test_gc_root_rebound_local.py`): **8 passed**.
- call-return/root precision/static-method cluster: **16 passed**, 19
  intentionally deselected by the focused expression.
- `tests/python/test_bootstrap_gate_baseline.py`: **2 passed**, 2 integration
  cases deselected by the repository's default marker policy.
- AArch64 system-assembler and pinned LLVM-MC instruction oracles: **2 passed**.
- Focused real self/no-libpython float program: **1 passed**.

The branch/fallthrough, while-zero, bignum comparison, `-2**63`, range escape,
owned-root cleanup, and runtime-value assertions are all exercised by the
45-case typed-int file. No arbitrary-precision behavior was replaced with raw
i64 wrap, no Dyn force-unboxing was introduced, and no libpython fallback was
enabled.

