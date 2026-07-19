# M2 NumPy native-extension / pcc-Python module graph

## Claim

`M2-NUMPY-MODULE-GRAPH` is `DONE_STRONG` for
host-current-source/self/pcc-native/no-libpython mode.

A source graph that imports a pcc-native extension can compile explicitly
imported `pcc/py_stdlib` providers as normal sibling modules. The C-API
`PyImport_ImportModule` path and pcc-Python imports therefore observe the same
cached module object and live attrs dictionary. Selection is generic and
contains no NumPy/requester-package dispatch.

## Focused gate

The mixed C-extension/pcc-Python regression imports and observes all required
builtin module boundaries (`math`, `sys`, `time`, `gc`, `copy`), while the
existing sibling regression covers a normal pcc-Python provider:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_extern_type_infer.py \
  tests/python/test_pcc_native_extension_loader.py::test_capi_import_sees_compiled_python_module_under_self_backend_no_libpython \
  tests/python/test_pcc_native_extension_loader.py::test_capi_import_sees_native_math_module_under_self_backend_no_libpython \
  tests/python/test_pcc_native_extension_loader.py::test_capi_import_sees_explicit_pcc_python_math_port_under_self_backend \
  tests/python/test_pcc_native_extension_loader.py::test_capi_import_sees_pcc_python_builtin_module_graph_under_self_backend \
  tests/python/test_py_stdlib_os_io_re_math.py \
  tests/test_numpy_head_gate.py tests/test_numpy_first_blocker.py
```

Observed: `22 passed in 7.37s`.

The module-namespace ordering regression also passed once:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_globals_no_libpython.py::test_globals_membership_excludes_later_module_assignment
```

Observed: `1 passed in 49.26s`. It was not repeated because this test's default
runtime-high compile path is substantially slower than the scoped loader gates.

## Real NumPy frontier

The already-fresh current-source and package-executor artifacts were refreshed
through the scoped loader gate. Both loader graphs compile
`math/sys/time/gc/copy/numpy.exceptions`, enter `PyInit` and `Py_mod_exec`, link
neither libpython nor LLVM, and now record:

```text
first_missing_module / Py_mod_exec / numpy._globals
```

- `build/head-truth/numpy-core/result.json`: PASS, loader refresh 1.842s.
- `build/head-truth/numpy-package/result.json`: PASS, loader refresh 1.863s.
- Both first-blocker lanes are stable at frontier 1; `math` is resolved history.

The loader-only command deliberately reuses an artifact whose full compile/link
surface is already recorded green. It avoids recompiling 136/137 NumPy objects
when only Python module-graph code changed.

## Claim boundary

This proves the shared module-object join and advances the pinned NumPy frontier
through `math` and `numpy.exceptions`; the focused C-API gate independently
proves `sys/time/gc/copy`. It does not prove `import numpy`, NumPy array runtime
semantics, or the package closure beginning at `numpy._globals`. Those remain
under `M2-NUMPY-L4`.
