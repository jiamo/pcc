# Investigation: pcc1 list-of-functions value-position lowering via syntactic FuncDef fallback

## Status
resolved

## Problem Description

`tests/python/test_pcc1_pytest_capable.py::test_pcc1_runs_test_list_via_indirect_calls`
failed with:

```
error: PCC-PY-COMPILE-001: Python pipeline requires libpython fallback
  for /tmp/.../prog.py (generated IR still calls py_cpy_* helpers);
  rerun with --python-libpython=auto/on or PCC_PYTHON_LIBPYTHON=auto/on
```

The test source:

```python
def test_arith() -> None:
    assert 2 * 3 == 6

def test_str() -> None:
    assert "ab".upper() == "AB"

def main() -> None:
    tests = [test_arith, test_str]
    passed = 0
    for fn in tests:
        try:
            fn()
            passed = passed + 1
        except AssertionError:
            pass
    print(f"{passed} passed")
```

The host pcc compiles this correctly: 0 `py_cpy_*` calls in the IR.
But pcc1 (the bootstrapped binary built from the same source) emits:

```
%f.fnptr.1.1 = bitcast ptr @user_lf_min_f to ptr
%.2 = call ptr @py_cpy_wrap_pcc_0arg(ptr %f.fnptr.1.1)
%.3 = call ptr @py_list_new(i64 1)
...
%.7 = call ptr @py_cpy_to_pcc_obj(ptr %.2)
call void @py_cpy_decref(ptr %.2)
```

— routing function value-position references through the libpython
wrap helper.

Root cause: `pcc/py_frontend/codegen/literal_lowering.py::_emit_list_literal`
gates the native callable path on:

```python
prefer_native_callables = isinstance(expr.ty, ListType) and isinstance(
    expr.ty.elem,
    FuncType,
)
```

In host pcc both `isinstance` checks pass and we route through
`_emit_native_func_value` (which uses `py_func_new_named`, no
libpython). In pcc1, `isinstance(expr.ty.elem, FuncType)` returns
`False` even though the inferred type IS a FuncType — pcc1's
compiled `isinstance` doesn't reliably reproduce cross-module
dataclass identity for the frozen dataclasses in
`pcc.py_frontend.py_ast`. (Earlier iter 23 documented this gap
and rejected a name-based fallback because it didn't help —
because the issue was actually downstream of the inferred type
not surviving across module boundaries in pcc1.)

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_pcc1_pytest_capable.py::test_pcc1_runs_test_list_via_indirect_calls \
  -q -n0
```

Pre-fix: pcc1 compile fails with the libpython-fallback gate.

## Test [CONFIRMED]

Same pytest case; pre-fix fails, post-fix passes (1 passed in 1.12s).

## Proposals

- No.1 Name-based fallback on element type's `name` attribute  [DENIED]
- No.2 Syntactic fallback on element shape (Name in self.functions)  [CONFIRMED]

## No.1 Name-based type fallback
### Code Change
Iter 23 attempt: accept either `isinstance(elem, FuncType)` or
`getattr(elem, "name", "") == "callable"`.

### DENIED
Didn't help — pcc1's type-info propagation seems to also leave the
element type bare-`DynType` (not `FuncType` at all) in some paths,
so even the name check fails.

## No.2 Syntactic FuncDef fallback
### Code Change

`pcc/py_frontend/codegen/literal_lowering.py::_emit_list_literal`:

```python
prefer_native_callables = isinstance(expr.ty, ListType) and isinstance(
    expr.ty.elem,
    FuncType,
)
if not prefer_native_callables and isinstance(expr.ty, ListType):
    all_func_names = bool(expr.elems)
    for _el in expr.elems:
        if not isinstance(_el, Name):
            all_func_names = False
            break
        if _el.ident not in self.functions:
            all_func_names = False
            break
    if all_func_names:
        prefer_native_callables = True
```

The fallback is bone-simple: if every element of the list literal
is a bare `Name` referring to a known user FuncDef
(`self.functions` is L1CodeGen's user-function table), route
through the native callable path regardless of the type-system
inference.

### CONFIRMED
- `tests/python/test_pcc1_pytest_capable.py::test_pcc1_runs_test_list_via_indirect_calls`
  passes (1 passed in 1.12s after pcc1 rebuild).
- Host pcc unchanged: `/tmp/lf_min.py` still produces 0
  `py_cpy_*` calls (the type-system branch fires first).
- `tests/python/test_pcc1_pytest_capable.py` 14 / 14.
- Fallback baselines + corpus: 206 passed, 4 skipped.
- Full 3-stage self bootstrap: 1 passed in 52.5s.

### Why this is correct
The native callable-value path uses `_emit_native_func_value`
(`py_func_new_named`) which is the standard pcc-native wrap. The
host-side `isinstance(elem, FuncType)` check was a (precise but
over-strict) type-system gate; the syntactic shape check is a
broader (but provably-correct) gate on the same idiom. If
*every* element is a bare Name referring to a known user
FuncDef, the list-element type is by definition a function
value — no inference is required.

## Report
Closes the pcc1 list-of-functions blocker tracked from iters
11–23. The syntactic fallback is intentionally conservative
(must be every element, must be a bare Name, must resolve into
`self.functions`) so it can't accidentally re-route legitimate
mixed-type lists. Iter 23's name-check approach is now removed
in favor of this syntactic check, which is both cheaper and
catches the real shape that fails in pcc1.
