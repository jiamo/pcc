# AUD-P1-PY-FRONTEND-LOWERING-CONSOLIDATION closure

Date: 2026-07-17

Claim boundary: consolidate the duplicated signed-i64 Python floor-division
arithmetic emitted by regular unboxed lowering and pure low IR. This does not
claim that subscript, comparison, or general DynType lowering is consolidated.

## Implemented boundary

- `emit_python_floordiv_i64_unchecked` is the sole owner of the signed
  `sdiv`/`srem` floor-correction algorithm.
- Regular unboxed lowering retains its catchable `ZeroDivisionError` guard
  before calling the shared owner.
- Pure low IR retains its existing admission rule: it calls the owner only
  when the divisor is a proven non-zero literal; variable divisors still bail
  to guarded full lowering.
- An AST architecture gate rejects migration of `sdiv`/`srem` floor behavior
  back into either caller. Host and pcc1 runtime regressions cover both paths,
  including negative floor correction.

## Gates

- Structure-only owner gate: `1 passed in 0.22s`.
- Native subscript/zero-division set, including the new host two-path
  regression: `7 passed in 2.82s`.
- Current-source pcc1 unsafe-i64 two-path smoke: `1 passed in 0.89s`, with
  output `-4`, `3`, `-4`, `-4`.
- Existing pcc1 `int_ops` oracle: `1 passed, 396 deselected in 65.44s`.
- Fallback and IR-Python ratchets: `25 passed in 243.84s`; no baseline was
  raised.

The former aggregate gate over all 397 self-host oracle cases found the
unrelated `dynamic_class_attr_function_instance_bound` pcc1 regression. The
same minimized program fails with both the pre-slice verified pcc1 and the
new current-source pcc1 (`AttributeError: __self__`), while host stage0 passes,
so the floor-div edit is ruled out causally. That regression is recorded as a
separate task rather than weakening its claim or expanding this slice.
