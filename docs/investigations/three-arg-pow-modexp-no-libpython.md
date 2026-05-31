# Investigation: pow(b, e, mod) (3-arg modular exponentiation) -> NameError under no-libpython

## Status
resolved (#45 — bootstrap biyrywrup PASSED 18/4skip, 155s)

## Problem Description
`pow(b, e, mod)` (3-arg modular exponentiation) raises runtime `NameError: name
'pow' is not defined`. `pow(b, e)` (2-arg) works. Found 2026-05-30 by real21.
3-arg pow is the standard modular-exponentiation primitive (crypto, number theory).

## Repro
```bash
printf 'def main():\n    print(pow(2,10,1000))\nmain()\n' > /tmp/p.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/p.py -o /tmp/p_bin
/tmp/p_bin            # NameError: name 'pow' is not defined
python3 /tmp/p.py     # 24
```

## Root cause (CONFIRMED)
`call_expression_lowering.py:789` handles `pow` only for `len(expr.args) == 2`
(routes to `_emit_binop_int("**")`). A 3-arg call falls through to the
unknown-name path -> runtime NameError.

## Proposals
- No.1 runtime fast modexp + route 3-arg pow to it   [IMPLEMENTED #45]

## No.1 runtime py_int_pow_mod (square-and-multiply) + frontend 3-arg pow
### Why NOT boxed (b**e) % mod
The tempting frontend-only fix `py_int_pow(b,e)` then `py_int_mod(.., mod)` is
CORRECT but computes the full `b**e` first — for crypto-size e (~2048 bits) that
intermediate is astronomically large -> OOM/hang. Unacceptable for the real use
of 3-arg pow. (It would be fine only for small e; not worth a half-fix.)
### Plan
Add `py_int_pow_mod(base, exp, mod)` doing square-and-multiply with bigint
mul+mod (never materialising the full power):
```
result = 1; base %= mod
while exp > 0:
    if exp & 1: result = (result * base) % mod
    base = (base * base) % mod
    exp >>= 1
return result
```
Locus: `py_int_ops.c` (next to `py_int_pow`, line 172) AND the pcc-Python port
`py_int_ops.py` (which *reimplements* py_int_pow at line 280 — DEFAULT mode links
the port, so the port needs the modexp too). Port caveat: pcc-Python has no
`break` (use a `done` flag in the while condition — see
[[feedback_pcc_python_break]]) and must use the boxed py_int_* ops (py_int_mul /
py_int_mod / py_int_shr / a low-bit test) with careful refcounting. Then route
`call_expression_lowering.py` `pow` with `len(args) == 3` to py_int_pow_mod
(box the three args, call, marshal back). Add a regression
(pow(2,10,1000)==24, pow(5,3,13)==8, plus a large-exponent case to prove no
intermediate blow-up) and run the FOREGROUND bootstrap.

## pending
Deferred from the 2026-05-30 session — a correct impl needs modexp in BOTH C and
the restricted pcc-Python port (refcount-careful loop). Clear, bounded; do it in
a focused session (not at the tail of a long one).

## Report (#45) — IMPLEMENTED, bootstrap pending
Resolved the C-vs-port placement by NOT touching py_int_ops at all: added a new
C-only helper `py_int_modexp.c` (`py_int_pow_mod(base, exp, mod)`,
square-and-multiply with the boxed py_int_mul/py_int_mod/py_int_and/py_int_shr +
py_int_cmp) and registered it in OBJ_PY_CC_HELPERS (linked in the no-libpython
archive, so no pcc-Python port reimplementation + no port refcount-loop risk).
py_runtime.h declares it; runtime_abi.py registers it; call_expression_lowering.py
routes `pow` with 3 positional args to it (boxes the three operands, marshals the
result int). pow_probe IDENTICAL; test_native_three_arg_pow.py 1 passed including
pow(2,100,1000000007) and pow(3,1000,97) (large exponents — proves no b**e
materialisation). FOREGROUND bootstrap auto-backgrounded by the harness
(biyrywrup); verdict on its completion.
