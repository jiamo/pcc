# Investigation: DynType boxed-float `-` / `*` / comparison against int or DynType operands produce wrong results (no py_obj_sub/py_obj_mul; falls to the i64 path)

## Status
resolved (fixes #26 `+`, #27 `-`/`*`, #28 comparison — all full-bootstrap-passed)

## Problem Description
Under strict no-libpython (`--backend self --python-libpython=off`, DEFAULT
ports), arithmetic on a *boxed float* (a `DynType` value that is a float at
runtime — an instance attribute, a true-division result, a dynamic return) is
WRONG for `-`, `*`, and comparison when the OTHER operand is an int literal or
another DynType. It is CORRECT when the other operand is a static float literal.

This is the sibling of fix #26 (py_float_add was a stub): #26 fixed `+`. `-`,
`*`, and comparison have a *different* root cause — there is **no `py_obj_sub` /
`py_obj_mul` runtime function at all**, and no DynType dispatch for `-`/`*` in
`binary_op_lowering`, so `DynType - int` / `DynType * int` fall through to
`_emit_binop_int`, which treats the boxed-float pointer as an i64 → garbage.

## Repro
```python
class Account:
    def __init__(self, balance):
        self.balance = balance        # 100.0 at runtime; static type DynType
    def with_fee(self, fee):
        return self.balance - fee     # DynType - DynType
def main():
    a = Account(100.0)
    print(a.balance - 10.0)   # pcc 90.0   CPython 90.0   OK   (DynType - FLOAT literal)
    print(a.balance - 10)     # pcc -10    CPython 90.0   WRONG (DynType - int)
    print(a.balance * 1.08)   # pcc 108.0  CPython 108.0  OK   (DynType * FLOAT literal)
    print(a.balance * 2)      # pcc 0      CPython 200.0  WRONG (DynType * int)
    print(a.balance < 50)     # pcc True   CPython False  WRONG (DynType < int)
    print(a.with_fee(5.0))    # pcc 0      CPython 95.0   WRONG (DynType - DynType)
main()
```

## Test [CONFIRMED]
Run 2026-05-30 (after #26). DynType-float op FLOAT-literal is diff-IDENTICAL
(the result is typed FloatType → `_emit_binop_float` → `_to_double` unboxes both
operands). DynType-float op int / DynType DIVERGES: the result is not typed
FloatType, so lowering falls to `_emit_binop_int`, which reads the boxed-float
pointer as an i64.

## Root cause
- `binary_op_lowering` has a DynType dispatch for `+` (-> `py_obj_add`, line
  ~379) and `/` (-> `py_obj_truediv`, line ~404), but NOT for `-` / `*`.
- `py_obj_sub` and `py_obj_mul` are not registered in `runtime_abi.py` and not
  defined in the runtime (port or C). `py_float_sub` / `py_float_mul` also do
  not exist (only `py_float_add`, which #26 just implemented).
- So `DynType - X` / `DynType * X` reach `_emit_binop_int` (raw i64 sub/mul) and
  misread the boxed pointer.
- Comparison (`<`,`>`,`<=`,`>=`) on a boxed float vs int similarly takes a
  non-float path and is wrong.

## Proposals
- No.1 mirror the `+` / `/` design: add `py_obj_sub` / `py_obj_mul` (port + C)
  that dispatch by tag (int->py_int_sub/mul bignum, float->py_float_sub/mul,
  complex, etc., raising TypeError otherwise) + implement `py_float_sub` /
  `py_float_mul` (port + C, mirror py_float_add: py_float_from_f64 of the f64
  op, numeric-guarded) + add DynType dispatch for `-`/`*` in binary_op_lowering
  (mirror the `+` block). For comparison, route DynType numeric compares through
  a float-aware path (py_obj_compare if it exists, or a tag-dispatched compare).
  [pending — the principled fix; multi-part runtime+frontend, one full bootstrap]
- No.2 narrower: only handle the `DynType (float) op int/DynType` numeric case
  by coercing to f64 when either operand is a runtime float. [pending]

## Scope / priority note
The most common boxed-float pattern — `attr * 1.08`, `attr - 0.5`, `attr > 0.0`
with a FLOAT literal — ALREADY works (result typed FloatType). The broken set is
boxed-float op int-literal / boxed-float op DynType (e.g. `balance * 2`,
`self.balance - fee`). Real but lower-frequency than the `+` case fix (#26) and
than the generator-consumption / print-instance classes already fixed this
session. The fix is mechanical (mirror py_obj_add / py_float_add) but multi-part
(2 new py_obj_* + 2 new py_float_* + dispatch + comparison), so it is a focused
slice of its own with a full bootstrap, not a tail-of-session patch. Found by
real7.py / inst_float.py via the realistic-program CPython-diff methodology.
Related: docs/investigations/typed-int-unboxed-overflow-silent-wraparound.md
(the typed-int side), and fix #26 (the `+` sibling).

## Update (2026-05-30): `-` and `*` RESOLVED (fix #27); comparison remains
Implemented Proposal No.1 for `-`/`*`: new `py_float_sub`/`py_float_mul` +
`py_obj_sub`/`py_obj_mul` (port + C) + binary_op_lowering DynType `-`/`*`
dispatch (after the native-set block). DEFAULT diff-IDENTICAL for boxed-float
`-`/`*` vs int / DynType, with no regression to set difference, list/str/tuple
repetition, boxed-int arithmetic, or typed arithmetic. Test:
tests/python/test_native_obj_sub_mul_float.py.

STILL OPEN: boxed-float COMPARISON (`<` `>` `<=` `>=`) against an int/DynType is
wrong (`a.balance < 50` -> True vs CPython False). This goes through the
comparison lowering / a compare runtime path, NOT py_obj_sub/mul. Next follow-up:
find the DynType numeric-compare path and route boxed-float compares through a
float-aware comparison (coerce both via py_float_to_f64 when either is a runtime
float), mirroring the `-`/`*` fix.

## Update (2026-05-30): comparison RESOLVED (fix #28) — investigation COMPLETE
Implemented for `<`/`<=`/`>`/`>=`: (1) port `_cmp_threeway` numeric-float case
(py_obj_ops_compare.py — the C already had it); (2) compare_membership_lowering
routes DynType ordering compares through py_obj_lt/le/gt/ge (after the FloatType
fast path, before the int path). DEFAULT diff-IDENTICAL incl regressions
(float-literal, int DynType, typed, str). Test:
tests/python/test_native_obj_compare_float.py. inst_float.py now FULLY identical.

The boxed-float arithmetic class is COMPLETE: `+` (#26), `-`/`*` (#27),
comparison (#28). All DynType numeric ops with a runtime float now match CPython. Investigation resolves once #28's full bootstrap passes (gating bqx6z4rv6); RESOLVED — #28 full bootstrap passed 18/4 in 445.12s.
