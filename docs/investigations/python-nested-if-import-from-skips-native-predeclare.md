# Investigation: `from .sibling import name` nested inside top-level `if/try` skips native predeclare

## Status
resolved

## Problem Description

`numpy/__init__.py:821` raises a runtime `NameError: name 'ones' is not defined` from the no-libpython pcc build, even though `ones` is explicitly imported at line 123 (inside the top-level `if not __NUMPY_SETUP__:` else-branch) and pcc's IR for `numpy/__init__.py` contains the matching `declare external ptr @user_numpy__core_numeric_ones(...)` extern.

Reduces to: pcc's declare-pass predeclare for native cross-module imports only runs for `ImportFrom` statements at the module top level. When the `ImportFrom` is nested inside a top-level `if`, `try`, `with`, `while`, or `for` block, `_prescan_nested_imports` walks the body but only seeds `_cpy_module_env` — it never calls `_predeclare_native_cross_module` — so `self.functions["<name>"]` is never bound for native sibling exports. A FuncDef inside the same block that calls one of those names then resolves the name through `name_lowering._lookup`, which falls through every native path and emits a static `py_raise(NameError("name '<name>' is not defined"))`.

This was carried as an unresolved "name-table propagation" hypothesis across several `docs/current-goal-state.md` entries (2026-05-28 19:30 closing the `_mat.__all__` module-global blocker noted "NameError 'ones' is the new downstream blocker"; 2026-05-28 20:30 narrowed to `_bind_native_cross_module_export` not reaching name resolution but did not identify the missing prescan branch).

## Repro

`tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_import_from_inside_top_level_if_predeclares_native_export`

```text
entry.py:
    import pkg

pkg/__init__.py:
    import os
    if os.environ.get('PCC_NEVER') != '1':
        from pkg._core import ones
        def _sanity_check():
            x = ones()
            print(x)
        _sanity_check()

pkg/_core/__init__.py:  from pkg._core.numeric import ones
pkg/_core/numeric.py:   def ones() -> int: return 1
```

Before the fix: `compile_python_multi(..., libpython_mode="off")` raises
`Python pipeline requires libpython fallback for multi-file compile (module pkg
generated IR still calls py_cpy_* helpers)`. Static IR contains
`@.name_error.<N> = "name 'ones' is not defined"`.

After the fix: `compile_python_multi(..., libpython_mode="off")` succeeds; the
exe prints `1`.

## Test [CONFIRMED]

- `tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_import_from_inside_top_level_if_predeclares_native_export -> 1 passed in 1.18s` (default LLVM backend, frontend-only test — the predeclare is backend-independent).
- `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> needs to be rerun with the fix applied.

## Proposals

- No.1 Add native cross-module predeclare branch to `_prescan_nested_imports`. [CONFIRMED]

## No.1 Add native cross-module predeclare branch to `_prescan_nested_imports`

### Code Change

`pcc/py_frontend/codegen/module_global_lowering.py::_prescan_nested_imports`:
between the existing `_register_native_builtin_import_from_aliases` early-return and the CPython-only `_cpy_module_global` loop, mirror the top-level branch from `generation_lowering.py:315-367`. The added block:

1. computes `native_table = self._native_module_exports` and the relative-import-resolved `resolved` module name;
2. for each imported name, checks if it's a native sibling submodule and registers the alias (`_register_native_module_alias`);
3. if all names were submodules, `continue` to the next pending statement;
4. if any remaining name is a native export of `resolved` (`_has_native_import_from_targets`), calls `_predeclare_native_cross_module(s, resolved, native_table.get(resolved, {}))` and `continue`;
5. otherwise falls through to the existing CPython module-global loop.

This is the exact same dispatching logic the top-level declare branch uses; without it the prescan was strictly weaker than the top-level branch.

### CONFIRMED

- Frontend test `test_import_from_inside_top_level_if_predeclares_native_export` passes; IR contains no `py_cpy_*` helper calls and no `name 'ones' is not defined` literal.
- The exe runs and prints `1`, confirming the native call dispatches.

## Report

This closes the `numpy/__init__.py:821 NameError: name 'ones'` blocker. The previous (2026-05-28 20:30) iteration suspected a name-table propagation bug between `_bind_native_cross_module_export` and name resolution; the actual mechanism is that `_bind_native_cross_module_export` was simply never called for the nested-if import. `_prescan_nested_imports` was the structural fork in the road — it ran for nested control-flow stmts but only handled the CPython fallback paths, mirroring just half of the top-level declare branch.

Related: `python-class-method-body-from-import-leaks-functions-binding.md` (sibling functions-table leak via class method body); `python-native-module-alias-module-global-attr-attribute-error.md` (the immediately-prior `_mat.__all__` blocker).

Follow-up (pre-existing bug, separate slice — not fixed here): `_prescan_nested_imports` uses `return` (instead of `continue`) at the scaffold / test-facade / unsafe / native-builtin-alias branches (lines 146-156). When a single nested ImportFrom matches one of those, the entire prescan abandons the rest of the pending queue. Not exercised by the current repro because the failure here was the missing native-cross-module branch, but worth tightening separately.
