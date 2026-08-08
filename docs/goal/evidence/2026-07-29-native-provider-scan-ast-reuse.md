# Native provider discovery preserves export-pass AST reuse

Date: 2026-07-29

Task: `BUG-P0-NATIVE-SUBPROCESS-CALLED-PROCESS-ERROR`

## Claim

Mandatory pcc-owned native-provider discovery no longer reparses every
multi-file source through `parse_and_lift(..., "<scan>")`. It uses the existing
masked lexical import scanner while retaining the `subprocess` provider edge
needed by shallow no-libpython bootstrap compilation.

This evidence proves the host multi-file AST-reuse contract and the
current-source `pcc0 -> pcc1` subprocess exit-status behavior. It does not claim
a new `pcc1 -> pcc2 -> pcc3` fixed point or cross-GC matrix result.

## Change

`_expand_required_native_builtin_providers` now reads each queued source once
and calls `_source_absolute_imports_for_discovery` with function bodies
included. Provider allowlisting, pcc-native source validation, deduplication,
and transitive queueing are unchanged.

## Gates

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_frontend_ir_pass_pipeline.py::test_compile_python_multi_reuses_export_pass_ast
1 passed in 0.65s

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_frontend_ir_pass_pipeline.py
81 passed in 3.69s

gtimeout 240s env -u LC_ALL uv run pytest -q -n0 tests/python/test_recursive_stdlib_compile.py tests/python/test_recursive_stdlib_import_codegen.py
37 passed in 5.33s

gtimeout 240s env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_compiled_pcc_multi_can_compile_toy_module
1 passed in 66.44s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_native_subprocess_no_libpython.py tests/python/test_native_subprocess_check_output.py
10 passed in 42.67s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 tests/python/test_pcc1_python_smoke.py -k 'subprocess and returncode'
1 passed, 57 deselected in 178.54s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
27 passed in 267.58s
```

The user-provided broad pre-fix run was `9579 passed, 1 failed`; its only
failure was the focused AST-reuse regression above. The complete broad suite
was not rerun after this minimal fix.
