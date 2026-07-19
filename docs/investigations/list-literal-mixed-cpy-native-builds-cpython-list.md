# Investigation: mixed cpy/native list literal wrongly built as an all-CPython list

## Status
resolved (2026-06-18)

## Problem Description
`test_native_list_literal_cpy_bridge.py::test_list_literal_with_cpy_value_bridges`
(both `off`/`on` scaffold modes) failed: a list literal `[p, x, p]` with a
native `str` `p` and a CPython-backed `x` (`decimal.Decimal`) was lowered as a
whole CPython list (`py_cpy_import("builtins").list()` + `py_cpy_getattr(append)`
per element, bridging the native `p` to cpy via `py_cpy_from_pccstr`). The
function is annotated `-> list`, so it should be a *native* pcc list with only
the cpy element bridged via `py_cpy_to_pcc_obj`.

## Repro
```python
import decimal
def f(p: str) -> list:
    x = decimal.Decimal("1")
    return [p, x, p]
# compile_python(..., emit_llvm_only=True, ir_scaffold_mode="auto", libpython_mode="on")
# wanted: @py_list_new + @py_list_append + @py_cpy_to_pcc_obj, no cpy.builtin.list / cpy.list.append
```

## Test [CONFIRMED]
After the fix, all four list-literal tests pass together:
`test_list_literal_with_cpy_value_bridges[off]/[on]`,
`test_splat_cpy_iterable_still_falls_back`,
and `test_cpython_compat_cext_import.py::test_cpython_list_of_cpy_values_builds_cpython_list`
(`numpy.concatenate([a, a])`). Native list literals under
`--backend self --python-libpython=off` still build native lists.

## Proposals

## No.1 Build the CPython list only for *all*-cpy element literals, not *any*

### Root cause
A regression from commit `32bfed70`. To fix `numpy.concatenate([a, a])` (a list
whose elements are *all* real CPython arrays — bridging them cpy->pcc->cpy loses
the arrays), `literal_lowering._emit_list_literal` was changed to
`cpy_any = cpy_extend or any(op[3] for op in ops)` — routing a literal with
**any** cpy element through `_emit_cpython_list_ops`. That over-triggers on a
*mixed* literal like `[p, x, p]`: it has a native `str`, so it is a native list,
but `any(cpy)` forced the whole thing cpy.

The two cases are genuinely complementary and both have tests:
`[a, a]` (every element cpy) → keep cpy; `[p, x, p]` (a native element present)
→ native, bridging just the cpy element. A native element cannot be kept cpy
without its own native->cpy round-trip, so its presence signals native-list
intent.

### Code Change
`cpy_any = cpy_extend or (bool(ops) and all(op[3] for op in ops))` — spread of a
cpy iterable, or **every** element cpy, builds the CPython list; otherwise the
native path builds `py_list_new`/`py_list_append` and bridges cpy elements via
`_emit_value_as_pcc_object_or_bridge` (`py_cpy_to_pcc_obj`). `bool(ops)` keeps
the empty literal native.

### CONFIRMED
All four list-literal tests pass. Provably inert in no-libpython (no cpy values
=> `op[3]` always False => `cpy_any == cpy_extend`, identical to before), so the
self-host bootstrap is unaffected; verified native list literals still work.

## Report
Minimal `any`->`all` predicate fix resolving a regression introduced by the cpy
concatenate support. The deeper principle (cpy vs native list is really about
the *consumer*) is approximated by all-cpy-vs-mixed, which satisfies both the
concatenate and the native-return tests. Sibling cpython-interop failure
`test_lambda_returning_cpython_object_stays_tagged` is a separate cpy-tag-across-
lambda-return gap, not addressed here.
