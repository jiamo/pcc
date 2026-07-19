# PKG-P0-PCC1-COMPAT-RUNNER closure evidence

`pcc1 --python-libpython=auto|on -m ...` now has a real, generic compatibility
execution owner: it invokes `PCC_COMPAT_PYTHON` (then `PCC_HOST_PYTHON`, then
`python3`) as an explicit CPython subprocess. Plain `-m` and
`--python-libpython=off` remain the strict pcc-native/no-libpython path.

The manifest is factual:

```text
requested_execution_mode=cpython-compat
execution_mode=cpython-compat
allows_libpython_fallback=true
links_libpython=false
native_package_claim=false
```

`links_libpython=false` describes the pcc1 artifact, not the external CPython
owner. `otool -L build/bootstrap-compat-runner-pcc1/pcc1` confirmed no Python
or libpython linkage.

Current gates:

```text
gtimeout 180s env -u LC_ALL PCC_DEBUG_STRICT_NOLIB_STUB=_run_python_module_from_pcc1_with_mode uv run pcc --backend self --python-libpython=off --ir-scaffold=on pcc/__main__.py -o build/bootstrap-compat-runner-pcc1/pcc1
exit 0 in about 57s; no strict no-libpython stub diagnostic

gtimeout 120s env -u LC_ALL PCC_CURRENT_PCC1=build/bootstrap-compat-runner-pcc1/pcc1 uv run pytest -q -n0 tests/python/test_pcc1_compat_runner.py
12 passed in 0.30s
```

The binary gate creates a generic temporary module, imports CPython's `math`
C extension, writes `cpython:math`, checks the exact compatibility manifest,
and regresses unchanged strict off/plain behavior. No package-name special
case, full bootstrap, GC matrix, or full GCC suite was used.
