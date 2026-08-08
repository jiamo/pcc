# Investigation: unannotated cross-module returns use mismatched ABIs

## Status

resolved

## Problem Description

An ABI audit performed while diagnosing the rebuilt Harness durable Session path found that unannotated functions had incompatible declarations across PCC modules. Reduced multi-file programs returned the wrong object or Python `None` even though the callee returned a live object.

PCC exports an absent Python return annotation as dynamic object type, but local function and class-method declarations treated the same absent annotation as explicit `-> None` and emitted `ret void`. A cross-module caller therefore declared a pointer-returning function while the definition returned no value. The caller observed a stale register value (`py_None` in this run), and the next field access used it as the SessionStore receiver.

This compiler defect was independently real, but it was not the final cause of the Harness `sessionStore` failure. After correcting the ABI and rebuilding, that product path still failed; the remaining cause was the constructor-initialized field export issue recorded in `pcc1-cross-module-constructor-field-type-export.md`.

## Repro

```bash
gtimeout 360s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_pipeline_exports.py \
  tests/python/test_extern_returns_none_abi.py \
  tests/python/test_py_cross_module_class_inference.py \
  -k 'returns_none or unannotated'
```

Source inspection confirmed the ABI split:

- `pipeline_exports._export_returns_none(None)` returns false and exports a dynamic pointer;
- `class_gen._declare_method` maps `FuncDef.return_ty is None` to LLVM `void`;
- `user_function_decl_lowering._is_none_semantic_type(None)` also maps a missing annotation to LLVM `void`.

## Test [CONFIRMED]

Add strict no-libpython multi-file regressions for an unannotated method and an unannotated top-level function. Both must return their object value across the module boundary, and emitted definitions must use pointer return types. Also exercise implicit fallthrough from an unannotated method and require the Python `None` singleton rather than the null error sentinel.

## Proposals

- No.1 Distinguish a missing return annotation from explicit `-> None` [implemented]

## No.1 Distinguish a missing return annotation from explicit `-> None`

### Code Change

Use the dynamic object ABI when `FuncDef.return_ty` is absent. Only explicit `NoneType` maps to LLVM `void`. Keep export schemas, imported declarations, local definitions, and legacy schema fallback aligned. For a pointer-returning method that reaches implicit fallthrough, return `py_None`; null remains reserved for the runtime error sentinel.

### Validation

The focused return/export group passed with 8 tests. Current pcc1 then self-bootstrapped from the revised sources, the rebuilt Harness passed both runtime and GUI self-checks, and the Cordis plus durable Session integration passed with 2 tests. The final pcc1 and Harness executables link only `libSystem`.
