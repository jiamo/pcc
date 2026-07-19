# DiagnosticSpan codegen adoption closure (2026-07-17)

Task: `AUD-P1-DIAGNOSTICSPAN-ADOPTION`

## Proven boundary

- The self-host codegen breadcrumb ring stores `DiagnosticSpan` objects rather
  than a separate preformatted span string.
- With `PCC_DEBUG_CODEGEN_PHASES=1`, the outer codegen boundary raises a
  `CodegenDiagnosticError` carrying the last breadcrumb span and the original
  exception type. The ordinary disabled trace path keeps the original exception.
- Host `observed_compile` and the pcc1 bootstrap formatter both prefer that
  precise span. The breadcrumb text spelling remains compatible.
- This proves precise span propagation for traced codegen failures. It does not
  claim every compiler diagnostic has an AST source span.

## Gates

- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/c/test_compile_observability_runtime.py tests/c/test_cli_observability.py tests/python/test_diagnostics_format.py`
  - `11 passed in 0.29s`
- `gtimeout 90s env -u LC_ALL uv run pytest -q -n0 tests/python/test_codegen_debug_trace.py -k 'breadcrumb_and_boundary_context or disabled_is_quiet'`
  - `2 passed, 1 deselected in 0.29s`
- `gtimeout 120s env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on --emit-llvm=/tmp/pcc-layer1-entrypoints-span.ll pcc/py_frontend/codegen/layer1_entrypoints.py`
  - passed
- `gtimeout 180s env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on pcc/__main__.py -o build/bootstrap-diagnostic-span-pcc1/pcc1`
  - passed; final rebuilt pcc1 completed in about 103 seconds
- `gtimeout 60s env -u LC_ALL PCC_CURRENT_PCC1=build/bootstrap-diagnostic-span-pcc1/pcc1 uv run pytest -q -n0 tests/python/test_codegen_debug_trace.py::test_codegen_trace_no_host_pcc1_enabled`
  - `1 passed in 0.32s`

The pcc1 gate sets `PCC_HOST_PYTHON=/usr/bin/false` inside the test and asserts
the final `PCC-PY-COMPILE-001` location is the failing source's `4:1`, not the
former input-only `0:0` span.
