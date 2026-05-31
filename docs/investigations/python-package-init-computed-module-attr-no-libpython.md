# Investigation: package __init__ computed module-level assignments don't bind as package attrs (no-libpython)

## Status
resolved 2026-05-31 — fix landed in pcc/py_frontend/pipeline.py multi-file
export classifier (one-line DynType fallback, mirroring the existing Name/Attr
treatment). e1/e2/e3 repro cases match CPython; regression
`tests/python/test_native_package_computed_init_attr.py` (3 passed); full
three-stage self-host bootstrap green (18 passed, 4 skipped). See No.1 + Report.
Gap originally CONFIRMED + precisely bisected 2026-05-31 by probing B-P0-PKG
pure-Python package-import shapes under `--backend self --python-libpython=off`.

## Problem Description
A package whose `__init__.py` binds a module-level name from a COMPUTED
expression (arithmetic or a function call) — e.g. `V = answer() + 8` or even
`V = 5 + 3` — fails: `import pkg; pkg.V` raises `AttributeError: V` under
no-libpython, while CPython prints the value. A STRING-LITERAL binding
(`VERSION = '1.0'`) DOES become a package attr (test_native_package_facade_import
passes), and relative imports / from-imports bind fine
(test_native_package_relimport passes). So the gap is specifically a
package-`__init__` module-level assignment whose RHS is a non-literal expression.

## Repro
```bash
site=/tmp/e1; rm -rf $site; mkdir -p $site/e1
printf 'V = 5 + 3\n' > $site/e1/__init__.py
printf 'import e1\ndef main():\n    print(e1.V)\nmain()\n' > /tmp/e1_main.py
PCC_PACKAGE_SITE=$site env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/e1_main.py -o /tmp/e1_bin
/tmp/e1_bin            # Traceback ... AttributeError: V
PYTHONPATH=$site python3 /tmp/e1_main.py   # 8
```

## Bisect (2026-05-31)
Built small packages under PCC_PACKAGE_SITE, compiled `import pkg`+access =off,
diffed vs `PYTHONPATH=$site python3`:
- `from pkg.core import answer` (absolute re-export in __init__) -> pkg.answer() = 42  ✓ IDENTICAL
- `from .core import answer` (relative) -> pkg.answer() = 42  ✓ IDENTICAL
- `from . import util` (relative submodule) -> pkg.util.K = 8  ✓ IDENTICAL
- `VERSION = '1.0'` (string-literal module-top) -> pkg.VERSION = '1.0'  ✓ (test_native_package_facade_import)
- `V = 5 + 3` (arithmetic module-top) -> pkg.V -> AttributeError V  ✗ DIFF (cpy 8)
- `V = f() + 8` (same-file call) -> pkg.V -> AttributeError V  ✗ DIFF (cpy 50)
- `V = answer() + 8` (imported call) -> pkg.V -> AttributeError V  ✗ DIFF (cpy 50)
- a computed assignment in a SUBMODULE (not __init__) -> COMPILE-FALLBACK (a related but distinct failure)
So: relative/absolute imports and string-literal module-top assignments bind as
package attrs; COMPUTED-RHS module-top assignments do NOT.

## Likely cause (unverified)
Cross-package attribute access (`pkg.V`) resolves V from the package module's
globals/attrs. The package-attr emission path (around `_module_globals` /
`_native_module_attr_global` in pcc/py_frontend/codegen/assignment_*_lowering.py
+ attr_*_lowering.py) appears to expose literal-RHS / import-bound module-top
names but not the result of a COMPUTED module-top assignment — either the
package `__init__` module-top computed statements are not executed/emitted in the
cross-package import path, or their results are not registered as package attrs.
A submodule (non-__init__) computed assignment fails differently (COMPILE-
FALLBACK), suggesting the package-module-top execution/attr-exposure path is the
locus.

## Test [N/A yet]
No gate added (characterizing the gap). A fix must add: a package with a computed
module-top __init__ binding compiles+runs native =off and `pkg.V` matches CPython;
plus a submodule computed binding; re-run the B-P0-PKG gate set + full bootstrap
(package module-top emission is shared with pcc's own multi-module compile, so
this is bootstrap-critical and NOT a clean additive slice).

## Proposals
- No.1 Register computed module-top assignments as DynType module_global exports  [CONFIRMED]

## No.1 Register computed module-top assignments as DynType module_global exports
### Root cause (IR-confirmed 2026-05-31)
The multi-file export classifier in `pcc/py_frontend/pipeline.py` (the loop that
builds `_native_module_exports[module][name] = info`) classifies a single-Name
module-top assignment by its RHS:
- `StrLit`/`IntLit`/`BoolLit`/`NoneLit` -> `kind=constant` (VERSION works);
- else `value_ty = _export_static_literal_type(value)`; if non-None ->
  `kind=module_global`; **if None -> NO export entry at all**.

`_export_static_literal_type` returns a type for literals, `Name`, `Attr`,
and literal containers, but **None for a computed RHS** (`BinOp` `5+3`, `Call`
`f()`). So `V = 5 + 3` produced no export. Cross-package `pkg.V`
(attr_load_lowering.py) then found no `module_global`/`constant` info and fell
through to the generic `py_obj_getattr(<module-name-string>, "V")` path, which
raised `AttributeError` — the module alias is the module NAME STRING in pcc's
compile, and a string has no attr `V`.

Crucially the module init **already** computes the value and stores it into the
`@.modvar.<mod>.V` slot (confirmed in the dumped IR: `_pcc_py_module_top_e1`
does `py_int_add(5,3)` then `pcc_gc_store_root(@.modvar.e1.V, ...)`). The value
was present at runtime; only the export-table classification (and therefore the
`pkg.V` resolution route) was missing.

### Code Change
`pcc/py_frontend/pipeline.py`, the `else` arm of the module-top `_Assign`
classifier: when `_export_static_literal_type(value)` is None for a real RHS,
fall back to `DynType("dyn")` instead of skipping, so the binding is registered
as a `module_global`. This exactly mirrors the existing Name/Attr DynType
treatment in `_export_static_literal_type` (added for the numpy
`__all__ = defmatrix.__all__` case): pcc cannot statically type the RHS, but the
binding is a real module global whose init code populates the `.modvar` slot, so
`pkg.V` now resolves via `_emit_native_module_global_attr_load` (extern
module-global load) instead of `py_obj_getattr`.

```python
else:
    value_ty = _export_static_literal_type(value)
    if value_ty is None and value is not None:
        from .py_ast import DynType as _DynType
        value_ty = _DynType("dyn")
    if value_ty is not None:
        exports[target_name] = {
            "kind": "module_global", "owning_module": mod_name,
            "export_name": target_name, "value_ty": encode_type(value_ty),
        }
```

### CONFIRMED
Repro cases now match CPython under `--backend self --python-libpython=off`:
- `V = 5 + 3` -> `pkg.V` = 8 (was AttributeError);
- `def f(): return 42` + `V = f() + 8` -> `pkg.V` = 50;
- `from pkg.core import answer` + `V = answer() + 8` -> `pkg.V` = 50.

Gates:
- `tests/python/test_native_package_computed_init_attr.py` — 3 passed (3.03s);
- existing package locks green: `test_native_package_facade_import.py`,
  `test_native_package_relimport.py`, `test_package_import_path.py`
  — 14 passed, 2 skipped (no regression on the literal/import paths);
- **full self-host bootstrap** (the classifier runs over pcc's own modules):
  `test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` +
  `test_bootstrap_gate_baseline.py` + `test_fallback_baseline.py` +
  `test_ir_py_fallback_baseline.py` — **18 passed, 4 skipped (147.86s)**.

## Report
Landed the one-line DynType fallback (No.1). It is a real B-P0-PKG advance, not
a capability-lock: a common real-package shape (computed version strings /
configs / registries bound at import in a package `__init__`) now imports and
resolves fully native under strict no-libpython, where it previously raised
`AttributeError`. The fix is minimal and consistent with prior art (the Name/Attr
DynType path), touches only the export classifier, and is bootstrap-safe (full
three-stage self-host green). A still-distinct, out-of-scope failure remains: a
computed binding in a SUBMODULE accessed via `from pkg import sub; sub.W`
COMPILE-FALLBACKs on the submodule-as-object import (`from pkg import submodule`
binding the submodule object), which is a different path from the package-`__init__`
attr-export classification fixed here. Regression-locked in
`tests/python/test_native_package_computed_init_attr.py`.

## Context
Found while probing B-P0-PKG package-import shapes after establishing the
numpy-import boundary is the C-ext ABI (see current-goal-state.md). Confirmed-
working shapes were regression-locked in test_native_package_facade_import.py
(facade re-export) and test_native_package_relimport.py (relative imports +
package class). This computed-module-top-attr gap is the one real gap that
probing surfaced; it is real and common (packages compute version strings /
configs / registries at import) but its fix is import machinery, not a clean
slice.
