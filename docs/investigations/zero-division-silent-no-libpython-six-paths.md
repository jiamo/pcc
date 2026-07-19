# Investigation: division/modulo by zero silently yields 0/inf/NULL instead of ZeroDivisionError (no-libpython, six lowering paths)

## Status
resolved

## Problem Description
Under strict no-libpython (`--backend self --python-libpython=off`), division and
modulo by zero did **not** raise a catchable `ZeroDivisionError`. Instead, depending
on the lowering path, `a // 0` / `a % 0` / `a / 0` produced a wrong value or an
uncatchable late crash:

- integer `//` (unboxed i64): ARM64 `SDIV` by zero yields **0** silently.
- integer `%` (unboxed i64): `SREM` by zero (UB).
- float `//` / `%`: `fdiv` / `fmod` yield **inf** / **nan** (no trap).
- dyn `%` (`py_obj_mod` → `py_int_mod`): returns **NULL without raising**
  (`py_int_mod` has `if (bv==0) return NULL;` and the comment defers the raise to
  the caller, but neither `py_obj_mod` nor codegen raised) → printed `<null>`,
  surfaced only at teardown as an uncaught `ZeroDivisionError`.
- typed-int boxed `//` / `%` (exact-int path → `py_int_floordiv` / `py_int_mod`):
  NULL, no raise.
- "pure" typed leaf functions: lowered via the **low_ir scaffold**, which emits a
  bare `sdiv`/`srem`/`fdiv` with **no error-exit at all** (it carries
  `post_call_error_check=None`).

Net effect: a defensive `try/except ZeroDivisionError` silently failed. This
violates Project Intent obligation 2 (performance/lowering must never weaken Python
semantics): silently returning `0` for `10 // 0` is a semantic corruption, not a
speed tradeoff.

Found 2026-05-30 by the realistic-program-diff method (real16 `safe_div`, then the
`divzero*` minimal probes).

## Repro
```bash
cat > /tmp/dz.py <<'PY'
def main():
    for a, b in [(10, 0), (10, 3)]:
        try: print('fd', a // b)
        except ZeroDivisionError: print('fd ZDE')
        try: print('md', a % b)
        except ZeroDivisionError: print('md ZDE')
        try: print('td', a / b)
        except ZeroDivisionError: print('td ZDE')
main()
PY
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/dz.py -o /tmp/dz_bin
/tmp/dz_bin            # before fix: "fd 0", "md <null>", "td <null>"
python3 /tmp/dz.py     # CPython:    "fd ZDE", "md ZDE", "td ZDE"
```

## Test [CONFIRMED]
`tests/python/test_native_zero_division.py` — inline-dyn, typed+cross-function,
bigint, float `//`, and the nonzero-constant-divisor (fast-path-preserved) cases.
Observed all three failing before the fix (`<null>` / `0` / `inf`), passing after.

## Proposals
- No.1 Guard all six division lowering paths     [CONFIRMED]

## No.1 Guard all six division lowering paths
### Code Change
Frontend-only (no runtime / Makefile rebuild). The runtime already sets the
exception where it can (`py_obj_truediv` raises directly); the bug was codegen not
trapping zero or not checking the NULL-on-zero return.

1. `binary_op_lowering.py`: new `_emit_zero_division_check(is_zero_i1, msg)`
   (raises ZeroDivisionError, mirrors `_emit_negative_shift_count_check`) and
   `_emit_zero_division_if_null(result, msg)` (NULL from `py_int_*`/`py_obj_mod`
   reliably means a zero divisor). Applied to: static `/` (fcmp guard), the
   `py_obj_mod` dyn path (err-check + null-check), `py_obj_truediv` dyn path
   (err-check — runtime already raises), and `_emit_runtime_int_binop_value`
   `//`/`%` (null-check). Float `_emit_binop_float` `//` and `%` got fcmp guards.
2. `expr_helper_lowering.py`: `_python_floordiv_i64` / `_python_mod_i64` — icmp
   `b==0` guard before `sdiv`/`srem`.
3. `exact_int_lowering.py`: boxed exact-int `//`/`%` — null-check after the
   `py_int_floordiv`/`py_int_mod` call.
4. `user_function_lowering.py`: the low_ir pure-leaf scaffold has no error-exit
   (`post_call_error_check=None`), so it cannot raise. `_low_ir_nonzero_literal()`
   keeps a division on the fast path only when the divisor is a provably-nonzero
   int/float literal (`x // 2`, `n % 256`); a variable/zero divisor returns None
   from `_low_ir_expr_to_value`, bailing the whole function to the guarded full
   lowering. Perf-neutral for constant-divisor hot paths; correct for the rest.

Message is `"division by zero"` everywhere — Python 3.14 unified the
ZeroDivisionError text (no more "integer division or modulo by zero" / "float
division by zero"); the diff oracle is Python 3.14.5.

### CONFIRMED
`tests/python/test_native_zero_division.py` 3 passed. The four `divzero*` probes
(inline-dyn, typed-cross-fn, typed-same-fn-try, bigint, float) all diff-IDENTICAL
vs python3. real15 no regression; real16's only remaining diff is the unrelated
nested f-string format-spec gap. Full no-libpython bootstrap: see baseline gate.

## Report
Landed Proposal No.1 (six-path guard, frontend-only). The key insight was that
no-libpython integer division has **six** distinct lowering paths (unboxed i64,
boxed runtime binop, exact-int boxed, dyn `py_obj_*`, float, and the low_ir
pure-leaf scaffold) — fixing any one in isolation leaves the others silently wrong.
The low_ir scaffold is the subtle one: it is the pure-leaf fast path with no
error-exit, so the fix there is to *exclude* trap-capable division rather than to
emit a raise it cannot route. Predecessor pattern: `_emit_negative_shift_count_check`
(negative shift count → ValueError) is the same guard shape for the same reason.

## Update 2026-06-18 — dyn `//` path was uncovered (follow-up fix)

A full-suite run surfaced `test_zero_division_inline_dyn` failing on `fd <null>`
(dyn `a // b`, both int, `b == 0`) while `md` (`%`) and `td` (`/`) raised
correctly. Root cause: the dyn `//` block in `binary_op_lowering.py` only did
`_emit_post_call_err_check(None)` after `py_obj_floordiv`, missing the
`_emit_zero_division_if_null(...)` that the dyn `%` path has. `py_obj_floordiv`
delegates INT // INT to `py_int_floordiv`, which returns NULL *without* raising on
a zero divisor (the float path in `py_obj_floordiv` raises directly, so float `//`
was fine — only int // int leaked NULL). Fix: mirror the mod path — add
`_emit_zero_division_if_null(fdiv_res, "division by zero")` after the err-check.
Verified: `tests/python/test_native_zero_division.py` 3/3 pass under
`--backend self --python-libpython=off`. This was the seventh lowering surface;
the "six paths" lesson holds — the dyn `//` guard had simply been omitted.
