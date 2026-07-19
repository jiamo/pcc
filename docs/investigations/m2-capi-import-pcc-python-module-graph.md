# Investigation: join C-API imports to the pcc-Python module object graph

## Status

resolved

## Problem Description

`M2-NUMPY-MODULE-GRAPH` requires a pcc-native extension's
`PyImport_ImportModule` calls and pcc-compiled Python package modules to resolve
through one module-object graph. The starting strict self/no-libpython NumPy
2.4.4 run entered `_multiarray_umath` PEP 489 `Py_mod_exec` and failed at
`PyImport_ImportModule("math")`; the next import was `numpy.exceptions`. Both
real lanes now resolve those providers and stop later at `numpy._globals`.

Predecessor:
`python-no-libpython-numpy-build-pcc-capi-include-redirect.md` established the
137-object build, extension load, PEP 489 execution, and the `math` then
`numpy.exceptions` sequence. The present task owns the generic object-graph
join; it must not add NumPy-name dispatch.

## Repro

The current real integration gate is deterministic:

```text
gtimeout 180s env -u LC_ALL uv run python scripts/numpy_head_gate.py run \
  --source projects/numpy-2.4.4 \
  --build-root build/head-truth/numpy-core \
  --result build/head-truth/numpy-core/result.json \
  --jobs 8 --compile-timeout 90 --link-timeout 90 --loader-timeout 120
```

Observed current result: gate PASS, loader exit 1, entered PyInit and
`Py_mod_exec`, first blocker exactly
`first_missing_module / Py_mod_exec / math`.

The minimized red test will compile one pcc-Python sibling module and one PEP
489 pcc-native extension whose exec slot calls `PyImport_ImportModule` for that
sibling, then reads a module attribute. CPython is the semantic oracle: both
imports must resolve to the same module namespace/object graph.

## Test [CONFIRMED]

The existing compiled-sibling bridge regression is green:

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_pcc_native_extension_loader.py::test_capi_import_sees_compiled_python_module_under_self_backend_no_libpython
```

Observed: `1 passed in 2.08s`.

The minimized native-builtin module-object regression is red:

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_pcc_native_extension_loader.py::test_capi_import_sees_native_math_module_under_self_backend_no_libpython
```

The main program explicitly imports `math`; a pcc-native extension then calls
`PyImport_ImportModule("math")`, looks up `floor`, and calls it. Compilation
succeeds, but execution exits 1 with
`PCC-PYEXT-IMPORT-001 ... module not found: math`. This isolates the missing
native-builtin module-object publication from the already-working compiled
sibling bridge.

## Proposals

- No.1 Register compiled native modules behind the C-API import-by-name seam [CONFIRMED existing]
- No.2 Materialize native-builtin module objects through the same registry [CONFIRMED]

## No.1 Register compiled native modules behind the C-API import-by-name seam

### Code Change

Source inspection found this mechanism already present:
`py_compiled_module_register_init` registers every compiled sibling before
eager top-level execution, `py_compiled_module_import_by_name` runs a guarded
top init on demand and returns a cached module object sharing the live attrs
dictionary, and `PyImport_ImportModule` consults it after extension lookup. The
existing `depmod`/extension regression confirms identity and attribute/call
behavior. No new change is needed for ordinary compiled siblings.

### CONFIRMED

The existing mechanism satisfies the generic pcc-Python sibling half of the
task. It does not publish compiler-recognized native builtins such as `math`,
which are aliases/direct lowerings rather than sibling modules.

## No.2 Materialize native-builtin module objects through the same registry

### Code Change

The first substitution explicitly passed `pcc/py_stdlib/math.py=math` to the
multi-file compiler. That isolated provider currently fails type inference at
its first extern wrapper: `return type mismatch: expected 'float', got
'extern'`. First make those pcc-Python wrappers explicit about conversion from
their extern result (`float(...)`, with Python-correct `int(...)` for
`floor`/`ceil`). Then rerun the explicit-provider regression before changing
automatic closure selection.

The eventual publication must preserve the direct-lowering path, remain generic
across native modules, and may not dispatch on NumPy or on a requesting package
name.

### Implemented

The explicit provider substitution exposed two generic frontend defects:

1. `extern(..., restype=c_double)` emitted `double` IR while type inference
   labeled calls as class objects. `type_infer.py` now mirrors codegen's
   `pcc.extern` aliases and preserves ABI-compatible result lanes.
2. `globals()` published every predeclared module slot, including assignments
   not executed yet. NumPy's `_is_loaded` guard falsely reported a reload.
   Module globals now carry runtime initialized flags, and only set slots enter
   the shared attrs dictionary.

`_expand_native_extension_module_object_ports` activates only when the source
graph imports a pcc-native extension. It adds only explicitly imported builtin
modules with real `pcc/py_stdlib` providers. `math`, `sys`, `time`, `gc`, and
`copy` compile as ordinary siblings and publish through the existing registry;
normal programs without an extension keep direct builtin lowering. The
selector contains no NumPy or requester-package name.

The `math` port gained `gcd`; `sys`, `time`, and `copy` consume existing
compiler/runtime primitives without libpython, and a small `gc` port consumes
the common collector kernel ABI. This grows the pcc-Python semantic layer
instead of adding five C semantic modules.

### CONFIRMED

The focused C extension imports all five builtin providers through
`PyImport_ImportModule` and reads a callable from each shared module object:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_pcc_native_extension_loader.py::test_capi_import_sees_pcc_python_builtin_module_graph_under_self_backend
```

Observed: `1 passed in 2.52s`. The combined focused set covering compiled
siblings, automatic/explicit math, the five-module graph, extern inference,
stdlib helpers, and NumPy ratchets reported `22 passed in 7.37s`.

Both already-fresh real artifacts were exercised through the scoped loader
refresh. Each loader compiled `math/sys/time/gc/copy/numpy.exceptions`, entered
`PyInit` and `Py_mod_exec`, linked neither libpython nor LLVM, and stopped at:

```text
first_missing_module / Py_mod_exec / numpy._globals
```

The `numpy-core-head` and `numpy-package-artifact` lanes are stable at frontier
1 with `math` in resolved history. Refreshes took 1.842s and 1.863s; the
136/137-object artifacts were not needlessly rebuilt for a loader-only graph
change.

## Resolution

`M2-NUMPY-MODULE-GRAPH` is complete. The next boundary is the broader NumPy
Python package closure beginning at `numpy._globals`; that belongs to
`M2-NUMPY-L4`.
