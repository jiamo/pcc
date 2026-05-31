# Investigation: Native module attribute writes still require libpython

## Status
resolved

## Problem Description
`tests/test_python_module_imports_parity.py::test_module_attribute_write` is
still xfailed.  The minimized program imports `sys`, writes `sys.my_added = 42`,
then reads `sys.my_added`.  In libpython-off mode this must compile without
`py_cpy_*` helpers, but the current lowering materializes the CPython `sys`
module and emits `py_cpy_setattr` / `py_cpy_getattr`.

## Repro
Run the xfailed test as a real assertion:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest \
  'tests/test_python_module_imports_parity.py::test_module_attribute_write' \
  -q -n0 --runxfail -vv
```

Expected current failure:

```text
PyPipelineError: Python pipeline requires libpython fallback ... (generated IR still calls py_cpy_* helpers)
```

## Test [CONFIRMED]
The repro above was run on 2026-05-08 and failed with the expected
`generated IR still calls py_cpy_* helpers` error.

## Proposals
- No.1 Store native builtin module attrs in compiler-owned PyObject* globals     [CONFIRMED]

## No.1 Store native builtin module attrs in compiler-owned PyObject* globals
### Code Change
Add `L1CodeGen` storage for `(module_name, attr_name)` pairs on native builtin
module aliases such as `sys`.  Lower `sys.x = value` to a GC-rooted PyObject*
global via `pcc_gc_store_root`, and lower later `sys.x` reads to that slot
instead of falling back to CPython getattr/setattr.

### CONFIRMED
Implemented in `pcc/py_frontend/codegen/layer1.py`:

- native builtin module attr slots are predeclared before module root entry;
- slots are included in module GC root registration and teardown;
- `sys.x = value` stores through `pcc_gc_store_root`;
- `sys.x` reads load the native slot and raise `AttributeError` if the slot is
  still unset.

The xfail marker was removed from
`tests/test_python_module_imports_parity.py::test_module_attribute_write`.

Observed result:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest \
  'tests/test_python_module_imports_parity.py::test_module_attribute_write' \
  -q -n0 -vv
# 1 passed in 0.92s
```

The containing file also passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest \
  tests/test_python_module_imports_parity.py -q -n0 -rxX
# 8 passed in 4.50s
```

## Report (only when the investigation is closing)
No.1 landed.  The fix keeps native builtin module attributes in pcc-managed
PyObject* globals rather than materialising CPython module objects, so the
libpython-off gate no longer sees `py_cpy_setattr` / `py_cpy_getattr` for the
minimized `sys.my_added` case.

B6 is only partially closed: `module.attr = val` is green, while
`module.fn(*args)` / `module.fn(**kwargs)` call splat remains tracked by the
roadmap.
