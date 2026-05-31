# Investigation: `float(<dyn>)` and `_to_double` re-introduce libpython linkage in closed-world build

## Status
resolved (2026-05-11)

## Problem Description

While verifying the `_maybe_emit_native_module_getattr` fix in
[pcc-py-codegen-nested-closure-genexpr-scope.md](pcc-py-codegen-nested-closure-genexpr-scope.md),
the multi-file closure probe (`scripts/probe_stage1_closure.py` with
`PCC_IR_SCAFFOLD=on`) failed in two distinct ways:

1. **Hard IR error**:

   ```
   LLVM IR parsing error
   error: argument is not of expected type 'double'
   %scaffold.Constant.2985.91 = call ptr (ptr, double)
       @user_pcc_llvm_capi_ir_scaffold_Constant_f64(
           ptr %_DOUBLE.2979.83, ptr %cpy.call1.float.2984.90)
                                ^^^ ptr where double expected
   ```

2. **Closed-world fallback regression** in the multi-file IR: 5 leftover
   `py_cpy_*` calls forming a single chain that pulled `libpython` back
   into the produced binary:

   ```
   py_cpy_import("builtins")
   py_cpy_getattr(builtins, "float")
   py_cpy_from_pcc_obj(<arg>)
   py_cpy_call1(float_fn, <arg>)
   py_cpy_to_f64(result)
   ```

The bootstrap test failed with:

```
error: PCC-PY-COMPILE-001: [python-frontend] Python pipeline requires
libpython fallback for multi-file compile (modules:
pcc.py_frontend.codegen.native_modules); rerun with
--python-libpython=auto/on
```

Both symptoms shared a single source pattern: pcc-Python source that
writes `float(value)` against a dyn-typed value.

## Repro

The exact source pattern is in `pcc/py_frontend/codegen/native_modules.py:773`:

```python
if value_kind == "float":
    value = info.get("value", 0.0)
    return self.builder.call(
        self.runtime["py_float_from_f64"],
        [ir.Constant(_DOUBLE, float(value))],
        name=self._fresh("native.const.float"),
    )
```

Here `value` is the result of `info.get("value", 0.0)`. `info` is a dict
of native-module export metadata; `info.get` returns DynType to the
type-inferer. `float(value)` is a runtime call to the Python builtin
against a DynType receiver — and that's the case both bugs hit.

Closure probe to reproduce:

```bash
env -u LC_ALL PCC_IR_SCAFFOLD=on uv run python scripts/probe_stage1_closure.py
```

Expected: `multi_ok=True, py_cpy_*=0`.
Observed before fix: IR parse failure / 5 leftover `py_cpy_*` calls.

## Test [CONFIRMED]

- `tests/python/test_pcc_self_host_getattr_default.py` and friends already
  cover the end-to-end self-host build. After the fixes:
  - `multi_ok=True, py_cpy_*=0` in the multi-file closure probe.
  - `./pcc1 → pcc2 → pcc3` proceeds past `native_modules` lowering.
  - `otool -L pcc1` reports zero libpython linkage.

## Bug 1: `_to_double(<PyObject*>, FloatType)` returns the PyObject* unchanged

`pcc/py_frontend/codegen/layer1.py::_to_double` (line ~30690):

```python
def _to_double(self, v: ir.Value, ty: Type) -> ir.Value:
    if isinstance(ty, FloatType):
        return v   # <-- BUG: blindly returns v regardless of IR shape
```

If `v` is a real native `double`, returning it is correct. If `v` is a
`PyObject*` (boxed float, e.g. from `py_cpy_call1` on the builtin
`float`), this returns the pointer unchanged. Downstream callers like
`_emit_scaffold_Constant_*` then pass that pointer as if it were a
native double and the IR pass fails to parse the resulting call.

### Code Change [CONFIRMED]

```python
def _to_double(self, v: ir.Value, ty: Type) -> ir.Value:
    if isinstance(ty, FloatType):
        if isinstance(v.type, ir.PointerType):
            if v in getattr(self, "_cpy_values", ()):
                return self.builder.call(
                    self.runtime["py_cpy_to_f64"],
                    [v],
                    name=self._fresh("cpy.to_f64"),
                )
            return marshal.marshal_from_object(
                self.builder,
                self.module,
                self.runtime,
                v,
                FloatType(name="float"),
            )
        return v
    ...
```

The PointerType branch chooses between the closed-world helper
`py_float_to_f64` (via `marshal_from_object`) and `py_cpy_to_f64` only
when `v` is already known to be a CPython value (`_cpy_values`).

## Bug 2: `float(<dyn>)` builtin lowering had no DynType branch

`pcc/py_frontend/codegen/layer1.py` (line ~21701):

```python
if name == "float" and len(expr.args) == 1:
    arg = expr.args[0]
    ty = arg.ty
    if isinstance(ty, FloatType):
        return self._emit_expr(arg)
    if isinstance(ty, (IntType, BoolType)):
        v = self._emit_expr(arg)
        return self._to_double(v, ty)
    if isinstance(arg, StrLit):
        folded = _maybe_fold_str_to_float(arg.value)
        if folded is not None:
            return ir.Constant(_DOUBLE, folded)
    # NOTHING handled DynType — fell through to generic builtin call
```

When `arg.ty` is `DynType` (as is the case for `info.get("value", 0.0)`),
the code fell through to the generic builtin-call path that emits
`py_cpy_import("builtins") + py_cpy_getattr("float") + py_cpy_call1(...)`.
That re-introduces libpython linkage even though `py_float_to_f64`
already handles the runtime conversion in the closed-world runtime.

### Code Change [CONFIRMED]

```python
if isinstance(ty, DynType):
    v = self._emit_expr(arg)
    if isinstance(v.type, ir.PointerType):
        return self._to_double(v, ty)
```

This routes `float(<dyn>)` through `_to_double` (which now correctly
unboxes the pointer to a native double via `py_float_to_f64`). The
runtime call disappears from the closed-world IR.

## Cross-reference

Triggering investigation:
[pcc-py-codegen-nested-closure-genexpr-scope.md](pcc-py-codegen-nested-closure-genexpr-scope.md).

The original investigation closed by changing
`_maybe_emit_native_module_getattr` to actually call `py_obj_getattr` on
dyn objects (previously it constant-folded the default). That fix caused
slightly different IR to be generated, surfacing the two bugs documented
here — both pre-existing but masked by the old constant-fold behaviour.

## Why this is "root cause" and not a workaround

Neither fix depends on the user rewriting source code:

- `_to_double` is the type-coercion helper for every Python operator and
  builtin that expects a `double`. Returning a `ptr` when `ty=FloatType`
  was an unsoundness that would have surfaced whenever a boxed float
  value flowed through that helper.
- The `float(<dyn>)` builtin lowering is the canonical place to handle
  the dyn case for that builtin. Without it, any user code that does
  `float(some_dyn)` outside a CPython context falls back to
  `py_cpy_call1`.

After both fixes:

- Multi-file closure (`PCC_IR_SCAFFOLD=on`): `py_cpy_*=0`.
- `--python-libpython=off` produces a binary with no libpython.
- `./pcc1 → pcc2 → pcc3` self-host bootstrap progresses without
  `native_modules` pulling libpython.
