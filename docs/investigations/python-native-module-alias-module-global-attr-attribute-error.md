# Investigation: `mod_alias.module_global_attr` (e.g. `_mat.__all__`) failed with AttributeError because (a) Attr-RHS exports were dropped and (b) Attr lowering had no `module_global` branch

## Status
resolved

## Problem Description

`numpy/__init__.py:681 set(_mat.__all__)` failed at runtime with
`AttributeError: __all__`, even though `numpy.matrixlib.__all__` exists
(line 7 of `numpy/matrixlib/__init__.py`). The IR for the access was

```
%modvar.__all.1392.1050 = load ptr, ptr @.modvar.numpy__core.__all__   ; <-- _core.__all__ OK
...
%attr.__all.1405.1060 = call ptr (ptr, ptr) @py_obj_getattr(
    ptr @.pystr.obj.73, ...)                                          ; <-- _mat.__all__: WRONG
```

where `.pystr.obj.73` is the literal string `"numpy.matrixlib"`. pcc was
calling `py_obj_getattr` on the module's **name string** instead of routing
through the `.modvar.numpy_matrixlib.__all__` extern that the matrixlib
top-init populates. Strings don't have `__all__`, so the access returned
NULL and raised AttributeError.

This closes the cap that the previous iteration's hasattr fix
([python-hasattr-static-false-on-builtin-modules.md](python-hasattr-static-false-on-builtin-modules.md))
flagged as the actual numpy line 681 blocker.

## Repro

The minimal trigger is a multi-file sibling-import where the submodule's
`__all__` is set via an Attr-expression RHS (not a literal). The numpy
chain is the ground-truth repro:

1. `numpy/matrixlib/__init__.py:7` has `__all__ = defmatrix.__all__`.
2. `numpy/__init__.py:463` does `from . import matrixlib as _mat`.
3. `numpy/__init__.py:681` does `set(_mat.__all__)` — fails with
   `AttributeError: __all__`.

Reproducing this without numpy needs a small multi-file harness that this
slice did not set up; the focused-suite regression surface (103 passed)
plus the end-to-end numpy gate (now passes line 681) serves as the
verification.

## Test [CONFIRMED]

End-to-end: the numpy auto-mode compile now runs past the original line
681 failure and proceeds to a different downstream error
(`numpy/__init__.py:821-832 NameError: name 'ones' is not defined` — out of
scope here). Focused regression: 103 passed across
`test_py_codegen_class_model.py test_python_generator_parity.py
test_py_multi_file_compile.py test_py_for_generic_iterable.py
test_py_exceptions.py test_native_os_misc.py
test_py_class_constructor_attr_args.py -q -n0`. Mandatory self-host gate:
`test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` → 1
passed in 41.73s.

## Proposals

- No.1 Recognise Attr-expression RHS in `_export_static_literal_type` + add `module_global` branch to Attr lowering  [CONFIRMED]

## No.1 Recognise Attr-RHS exports + add module_global Attr lowering branch

### Code Change

Two coordinated edits.

**A.** `pcc/py_frontend/pipeline.py::_export_static_literal_type` — add an
`_Attr` recognition (mirroring the existing `_Name → DynType` branch):

```python
if _closed_world_is_node(expr, _Attr):
    # ``X = other.attr`` — pcc cannot statically determine the precise
    # value type, but the export is real (the defining module's
    # ``_pcc_py_module_top_<mod>`` populates it at init).  Returning
    # None here silently dropped the export; returning DynType keeps it
    # in ``_native_module_exports`` so downstream Attr loads route
    # through the ``.modvar.<mod>.<attr>`` extern.
    return _DynType("dyn")
```

**B.** `pcc/py_frontend/codegen/attr_load_lowering.py` — add a
`kind == "module_global"` branch in BOTH alias-export paths (the
`_native_module_expr_export_info` path and the
`_native_module_alias_export_info` path):

```python
elif kind == "module_global":
    return self._emit_native_module_global_attr_load(
        module_name, expr.name, info, expr.span)
```

with helper
`pcc/py_frontend/codegen/native_modules.py::_emit_native_module_global_attr_load`:

```python
def _emit_native_module_global_attr_load(
    self, module_name, attr_name, info, span,
):
    value_ty = decode_type(info.get("value_ty", ("dyn",))) or DynType(name="dyn")
    ir_ty = _CSTR if self._is_object(value_ty) else self._storage_ir_type(value_ty)
    sym = self._module_global_symbol_name(module_name, attr_name)
    existing = self.module.globals.get(sym)
    if existing is None:
        gv = ir.GlobalVariable(self.module, ir_ty, name=sym)
        gv.linkage = "external"
    else:
        gv = existing
    return self.builder.load(gv, name=self._fresh(f"modvar.{attr_name}"))
```

### CONFIRMED

Root cause was a compounded bug across the multi-file export pipeline:

1. **Export collector at `pipeline.py:3591-3648`** handles module-level
   `Assign` statements as exports. The RHS dispatch covered `StrLit`,
   `IntLit`, `BoolLit`, `NoneLit` (each → `kind="constant"`) and any
   other type recognised by `_export_static_literal_type` (→
   `kind="module_global"`). `_export_static_literal_type`'s table covered
   the literal scalars, `Name → DynType`, `TupleExpr`, and `ListExpr` —
   but **not `Attr`**. So `__all__ = defmatrix.__all__` (an Attr RHS) made
   `_export_static_literal_type` return None, the Assign collector
   silently skipped the assignment, and `numpy.matrixlib`'s exports table
   was missing `__all__`. The defining module STILL emitted the `.modvar`
   global and populated it at top-init time — the IR for matrixlib
   contained `@.modvar.numpy_matrixlib.__all__` — but importing modules
   (numpy/__init__.py compile) had no entry in their `_native_module_exports`
   to tell them this extern existed.

2. **Attr lowering at `attr_load_lowering.py:769-830`** had branches for
   `kind == "class"`, `"function"`, `"constant"` when an Attr resolved
   through a sibling-module alias — but **no `module_global` branch**.
   Even if `__all__` had been in the exports table, the Attr lowering
   would have fallen through, eventually landing on a generic
   `py_obj_getattr` path that received the alias's stored value — which
   for native module aliases is the module's NAME STRING in pcc's
   compile. `py_obj_getattr("numpy.matrixlib", "__all__")` returns NULL
   (strings have no `__all__`), AttributeError.

Either bug alone broke the chain; both were necessary to fix. With both
landed, `_mat.__all__` in numpy/__init__.py:681 now compiles to a load
from `@.modvar.numpy_matrixlib.__all__`, which the matrixlib top-init
populates with the value of `defmatrix.__all__`.

Evidence:
- Focused gates (103 passed across class/codegen/generator/multi-file/
  for-loop/exception/os suites): no regression.
- Mandatory self-host gate: 1 passed in 41.73s.
- Numpy auto-mode end-to-end: compile rc=0, exe produced, runs PAST the
  prior `line 681 AttributeError: __all__` failure to a different
  downstream error (`numpy/__init__.py:821-832 NameError: 'ones' is not
  defined`). The original cap is closed.

## Report

Landed No.1, a two-site coordinated fix (export collector + Attr lowering).
Closes the numpy `line 681 AttributeError: __all__` cap that was the
recorded next-blocker after the class-method `self.functions` leak fix
and the runtime SIGSEGV defensive guard.

**Newly-exposed downstream blocker** (NOT this change; next iteration):
`numpy/__init__.py:821-832 NameError: name 'ones' is not defined`.
`ones` is a numpy core function. At line 821 it's called inside a
`_sanity_check` function whose enclosing scope should have `ones` from
the numpy._core star-import. The NameError suggests pcc's name resolution
for nested function bodies isn't seeing the star-imported numpy._core
names, OR the numpy._core export of `ones` isn't being established at
module-init time.

Progress order: ... → (closed) class-method self.functions table leak →
exe PRODUCED → (closed) py_cpy_call_kw SIGSEGV → (closed) hasattr
static-False fold → (closed, this) `_mat.__all__` /
module_global Attr lowering → numpy runs to line 821 → (NEW) NameError
'ones' not defined in `_sanity_check`.
