# Python user-tooling source and focused evidence

Mode: host-pcc unit/contract checks plus standalone C syntax/argv harness. This
is not installed-current-pcc1, integration, or fixed-point evidence.

The bootstrap CLI now owns script, `-m`, `-c`, and stdin request routing without
invoking CPython. A private, validated startup envelope separates the physical
compiled executable from the Python-visible `sys.argv[0]`, publishes exact
program arguments and exit status, and distinguishes script/module/command/
stdin entry modes. The C runtime and pcc-Python runtime port mirror the startup
state, including the optional libpython argv synchronization hook. `sys.path[0]`
and `sys.executable` use the logical entry mode and physical executable
respectively.

Added required gate sources:

- `tests/python/test_pcc1_python3_cli_matrix.py`
- `tests/python/test_pcc1_traceback_tooling.py`
- `tests/integration/test_installed_pcc1_repl_debug_profile.py`

The CLI publishes a machine-readable, mode-labelled tooling manifest. Until
runtime hooks exist, interactive REPL, line debugging, runtime profiling,
and coverage fail closed with stable diagnostics; they are not described as
supported and never delegate to CPython.

The warnings surface is now a real pcc-owned provider rather than a lowering
no-op. It implements ordered filters (`ignore`, `default`, `always`, `error`,
`once`, and `module`), `filterwarnings`, `simplefilter`, `resetwarnings`,
`warn_explicit`, nested recording contexts, formatting, and stderr emission.
`warn(..., stacklevel=N)` intentionally uses the stable `<pcc>:0` boundary
until compiled-frame discovery can supply an exact caller; callers that know a
location use `warn_explicit`.

Closing this path exposed and fixed a generic compiler bug: calls to a
recursively compiled module function that omitted defaults fell through to a
CPython import. Such calls now use the already-published native function object
and its signature binder, preserving callee-owned default expressions without
libpython. The focused warnings closure contains zero `call @py_cpy_*` sites.

Focused commands completed:

```text
gtimeout 30s env -u LC_ALL PYTHONPYCACHEPREFIX=/tmp/pcc-pycache-tooling uv run python -m py_compile <changed Python sources and the three required test files>
PASS

gtimeout 30s cc -std=c11 -fsyntax-only -I pcc/py_runtime/include -I pcc/py_runtime/src pcc/py_runtime/src/py_process.c
PASS

gtimeout 30s cc -std=c11 -fsyntax-only -I pcc/py_runtime/include -I pcc/py_runtime/src pcc/py_runtime/src/py_os_substrate.c
PASS

gtimeout 30s cc -std=c11 -fsyntax-only -I pcc/py_runtime/include -I pcc/py_runtime/src pcc/py_runtime/src/py_process_substrate.c
PASS

gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_pcc1_python3_cli_matrix.py tests/python/test_pcc1_traceback_tooling.py tests/python/test_cli_bootstrap_observability.py tests/python/test_cli_contract.py tests/python/test_cli_shared_helpers_contract.py
53 passed in 0.57s

gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_pcc1_traceback_tooling.py tests/python/test_recursive_stdlib_import_codegen.py::test_recursive_off_mode_routes_warnings_filter_calls_native
7 passed in 0.65s
```

Open boundary: the installed-current-pcc1 integration source has not run, and
exact `warn(..., stacklevel=N)` caller attribution plus true
REPL/debugger/profiler/coverage ownership remain unimplemented. Optimized
package, virtual-thread, and native-extension traceback frames also require the
listed integration/final stage gates before the row can become `DONE_STRONG`.
