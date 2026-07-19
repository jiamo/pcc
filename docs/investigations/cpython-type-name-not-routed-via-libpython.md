# Investigation: `type(cpy).__name__` routed via native getattr instead of libpython

## Status
resolved (2026-06-18)

## Problem Description
In libpython mode, `type(a).__name__` where `a` is a CPython-backed value
(e.g. `numpy.arange(3)`) loaded `.__name__` through native `py_obj_getattr`
instead of `py_cpy_getattr`, so the test
`test_cpython_compat_cext_import.py::test_cpython_type_name_inline_dispatches_via_libpython`
failed (`@.cpy.attr.__name__` absent from the IR). `py_obj_getattr` uses pcc's
native type model and mishandles a real CPython type object.

## Repro
```python
import numpy
def f():
    a = numpy.arange(3)
    return type(a).__name__
# compile_python(..., emit_llvm_only=True, ir_scaffold_mode="auto", libpython_mode="on")
# before: type(a) -> py_cpy_getattr(__class__) [cpy], then .__name__ -> py_obj_getattr [native]
```

## Test [CONFIRMED]
`test_cpython_type_name_inline_dispatches_via_libpython` passes; `@.cpy.attr.__name__`
now in the IR. Native (non-cpy) `type(x).__name__` still uses the
`py_obj_type_name` fast path (verified `type(C()).__name__ == "C"`,
`type(5).__name__ == "int"` under `--backend self --python-libpython=off`).

## Proposals

## No.1 `_expr_looks_cpython(type(x))` must follow the argument

### Root cause
`attr_load_lowering._emit_attr` correctly *skips* the native
`type(x).__name__` fast path when the argument looks cpython (gated on
`not self._expr_looks_cpython(expr.obj.args[0])`), intending to fall through to
the "cpy Call-receiver" branch which routes via `py_cpy_getattr`. That branch
(`isinstance(expr.obj, (Attr, Subscript, Call)) and self._expr_looks_cpython(expr.obj)`)
requires `_expr_looks_cpython(type(a))` to be true. But in
`cpy_return_analysis._expr_looks_cpython`, the `Call` case for `type(a)` fell to
`return self._expr_looks_cpython(expr.func)` — it tested whether the *function*
`type` is cpy (always False), not the *argument*. So the branch was skipped and
`.__name__` fell through to native `py_obj_getattr`. (`_emit_type_builtin`
already tags its result cpy; the problem was purely the structural predicate not
recognizing `type(cpy)` as a cpy-producing call.)

### Code Change
In `_expr_looks_cpython`'s `Call`/`Name`-func branch, add
`if expr.func.ident == "type" and len(expr.args) == 1: return
self._expr_looks_cpython(expr.args[0])`, mirroring the existing `getattr` case.
`type(cpy)` resolves to the real CPython type object, so its attribute loads
must route through libpython.

### CONFIRMED
Inert in no-libpython (the argument never looks cpy there, so the branch returns
False and the native `type(x).__name__` fast path still fires — verified). Only
`type(cpy).…` attribute loads change, gaining correct libpython routing.

## Report
One-branch structural-predicate fix. Note: the sibling cpython-interop failures
`test_native_list_literal_cpy_bridge` (element-wise cpy→native list bridging)
and `test_lambda_returning_cpython_object_stays_tagged` (lambda return value
losing its cpy tag) are *separate* deeper cpy-tracking gaps, not addressed here.
