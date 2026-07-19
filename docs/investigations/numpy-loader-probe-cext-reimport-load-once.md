# NumPy head-truth loader probe fails on cext re-import ("load once per process")

## Symptom

`scripts/numpy_head_gate.py run` (README numpy step 2) exits non-zero with
`status: FAIL`, `loader.entered_pyinit=False`, `loader.entered_py_mod_exec=False`,
and:

```
File ".../numpy/__init__.py", line 120, in <module>
ImportError: cannot load module more than once per process
```

The build itself is clean: `compile.passed=137 / failed=0`, `link.returncode=0`,
`link.exports_pyinit=True`, `link.links_libpython=False`. The failure is only in
the post-build loader probe.

## Two prior mislabels corrected

1. **NOT the Xcode SDK / `_LIBCPP_ENABLE_ASSERTIONS` blocker.** That flag drift is
   already handled by `build_exec._normalize_stale_toolchain_flags`
   (`compile.toolchain_normalizations` is populated and all 137 numpy C/C++
   objects compile). The 2026-07-18 task-board note attributing step 2's failure
   to the SDK was wrong.
2. **NOT fixed by exposing `ImportError.msg`.** numpy's `_core/__init__.py`
   treats that exact message as a deliberate re-init opt-out and **re-raises**:

   ```python
   if exc.msg == "cannot load module more than once per process":
       raise
   ```

   So faithful `.msg` makes numpy's own branch evaluate correctly — and its
   correct evaluation is to re-raise. The gate stays red.

## Root cause (confirmed direction; exact bypass still open)

The probe source (`_loader_probe`) imports several `numpy._core.*` submodules and
then `import numpy._core._multiarray_umath` explicitly. The first
`import numpy._core._exceptions` already loads `_multiarray_umath` transitively
(via `numpy/_core/__init__.py` -> `from . import multiarray`). numpy's C module
has a process-global "loaded once" flag (`multiarraymodule.c`); when `PyInit` runs
a **second** time in the same process it raises "cannot load module more than
once per process".

Under CPython the second `import` returns the `sys.modules`-cached module without
re-running `PyInit`, so `PyInit` runs once. Under pcc the second import re-invokes
`PyInit`. `pcc/py_runtime/src/py_extension_loader.c` **does** have a
`(module_name, path)`-keyed cache (`pcc_extension_find` /
`pcc_extension_find_module`), yet the second init still fires — so one of the two
import sites reaches `PyInit` on a path that bypasses that runtime cache (leading
hypothesis: the closed-world compile lowers the explicit
`import numpy._core._multiarray_umath` to a compiled-in direct init call rather
than a `py_native_extension_import*` runtime lookup). **This exact bypass is not
yet pinned down** and is the open work.

## Test [CONFIRMED]

- Step-2 failure and its real reason:
  `env -u LC_ALL uv run python scripts/numpy_head_gate.py run` -> `status FAIL`,
  loader stderr = the "load once per process" ImportError at
  `numpy/__init__.py:120`; `compile.passed=137, failed=0`.
- The `.msg` gap that made numpy's branch misbehave (now fixed):
  `getattr(ImportError("x"), "msg", "NO")` returned `NO` under pcc vs `"x"` on
  CPython. After the fix (below) it returns `"x"`, and non-ImportError exceptions
  still have no `.msg`, byte-identical to CPython (default/port and
  `PCC_RUNTIME_CC=cc` runtime modes).
- Real single-import capability is unaffected and works: a program doing
  `import numpy as np; np.array([1,2,3]) + 1` compiles under
  `pcc1 --backend self --python-libpython=off` and runs (`2.4.4`, `[2,3,4]`), no
  libpython, GC0..4.

## Landed in this investigation

`ImportError` / `ModuleNotFoundError` now expose `.msg` (== args[0]), scoped to
those classes so a bare `RuntimeError`/`ValueError` keeps no `.msg` (CPython
faithful). Mirrored in both runtime tiers:

- C: `pcc/py_runtime/src/py_obj_ops_dispatch.c` `py_obj_getattr` `PY_TYPE_EXC`
  branch, gated by `py_isinstance(o, py_exc_builtin_class(PY_EXC_IMPORTERROR))`.
- Port: `pcc/py_runtime/py/py_obj_ops_dispatch.py` same branch + `_cstr_is_msg`
  helper + `py_exc_builtin_class` extern.

This is a genuine CPython-compat correctness fix (it does not by itself turn the
gate green — see above).

## Update 2026-07-18: layer 1 root-caused by trace and FIXED (register-before-exec)

Instrumented `py_extension_loader.c` with an env-gated diagnostic
(`PCC_DEBUG_EXT_IMPORT=1`, kept as a deliberate off-by-default probe like
`PCC_DEBUG_BAD_BACKTRACE`) and compiled the exact loader-probe source. Trace of
the failure:

```
[ext-import] name=numpy._core._multiarray_umath cached=0 path=<site .so>
[ext-import] exec-begin name=numpy._core._multiarray_umath
[ext-import] by-name ... (mid-exec Python import cascade)
[ext-import] name=numpy._core._multiarray_umath cached=0  <-- SAME name, SAME path
ImportError: cannot load module more than once per process
```

Same name, same path — the cache key was never the issue. The loader registered
the module only AFTER `PyInit` + all `Py_mod_exec` slots completed, so a nested
import of the same module from inside exec (numpy's exec imports Python modules,
which import `_multiarray_umath` back) missed the cache and re-ran `PyInit`.
CPython's PEP 489 contract puts the module into `sys.modules` BEFORE
`exec_module`.

Fix (both landed):

- `pcc/py_runtime/src/py_extension_loader.c`: cache lookup keyed by fully
  qualified module NAME (sys.modules semantics; the removed `(name, path)`
  exact-pair lookup would also have missed legitimate same-module re-imports
  resolved through a different site path), and — the actual fix —
  `pcc_extension_register(...)` now runs BETWEEN module creation and the exec
  slots, with `pcc_extension_unregister(...)` rollback on exec failure.
- `pcc/py_runtime/src/py_capi_shim.c`: `pcc_capi_module_exec` split into
  `pcc_capi_module_from_def` + `pcc_capi_module_run_exec_slots` so the loader
  can interleave registration (compat wrapper kept; `py_libpython.c` stubs
  mirrored; declarations in `py_internal.h`).

Post-fix trace: nested imports hit `cached=1`, "load module more than once" is
GONE. Regression gates: `tests/python/test_pcc_native_extension_loader.py` ->
86 passed; both package E2E gates (wheel + numpy single-import user path) ->
2 passed.

## Layer 2: parent packages were not initialized before their children — root-caused by LLDB and FIXED

With re-init fixed, the probe failed later: `AttributeError:
_ArrayFunctionDispatcher` (the from-import at `numpy/_core/overrides.py:7`).
LLDB backtraces on `py_native_extension_import` and `py_obj_missing_attr`
pinned the exact mechanism (generated `_pcc_py_module_top_*` frame names make
the compiled import cascade readable):

- CPython's import contract is parent-before-child: `import a.b.c` fully
  initializes `a`, then `a.b`, before touching `a.b.c`. pcc's compiled import
  lowering initialized ONLY the named module. The probe's
  `import numpy.exceptions` therefore never ran `numpy/__init__` at all.
- Consequently the whole-package body was available to be triggered from
  arbitrary depth. Concretely: `_multiarray_umath`'s exec at
  `multiarraymodule.c:4963` (`initialize_static_globals`) does
  `npy_import("numpy._core._exceptions", "_ArrayMemoryError")`; resolving that
  name initialized the never-run `numpy._core/__init__` MID-EXEC, which runs
  `multiarray.py` -> `overrides.py`, whose from-import reads
  `_ArrayFunctionDispatcher` before the exec slot registers it at line 5149.
  Under CPython that import finds `numpy` / `numpy._core` already in
  sys.modules (in progress), so only the leaf `_exceptions.py` (which is
  light) executes mid-exec.

Fix (CPython parent-before-child in both import roots):

- `pcc/py_runtime/src/py_capi_shim.c`:
  `py_compiled_module_ensure_parent_packages()` runs the guarded top-init of
  each '.'-prefix parent (in-progress parents return early, preserving
  partial-module semantics); `py_compiled_module_import_by_name` now calls it
  before the module's own init. Declared in `py_runtime.h`; stubbed in
  `py_libpython.c`.
- `pcc/py_runtime/src/py_extension_loader.c`: both
  `py_native_extension_import` and `..._by_name` initialize compiled parent
  packages after a cache miss (re-checking the cache afterwards, since a
  parent's body may import the extension itself). This mirrors CPython loading
  a submodule extension only after its packages exist, so a cext's exec-time
  imports back into the package see partial-module state instead of running
  whole package bodies late.

## Verification [CONFIRMED]

- Loader-probe replica (`PCC_RUNTIME_CC=cc PCC_RUNTIME_HIGH=c`, head-truth
  site): prints `numpy-core-import-complete`, exit 0.
- Full gate: `env -u LC_ALL uv run python scripts/numpy_head_gate.py run` ->
  exit 0, `status PASS`, `entered_pyinit=True`, `entered_py_mod_exec=True`,
  compile 137/0, no libpython/LLVM. The `--skip-loader` workaround added
  earlier the same day was removed again (README step 2 is plain `run`).
- Regressions: `tests/test_numpy_head_gate.py` 8 passed;
  `tests/python/test_pcc_native_extension_loader.py` 86 passed; package E2E
  gates (wheel full pipeline + numpy same-skeleton) 2 passed.
- `PCC_DEBUG_EXT_IMPORT=1` stderr tracing in `py_extension_loader.c` is kept
  as a deliberate off-by-default diagnostic (it is what made both layers
  diagnosable).

## Status

RESOLVED (both layers) at the focused-gate level: layer 1 = register-before-exec
(PEP 489 sys.modules-before-exec) + name-keyed load-once cache; layer 2 =
parent-package-before-child in the compiled-module and extension import roots.
The runtime edits (`py_extension_loader.c`, `py_capi_shim.c`,
`py_obj_ops_dispatch.c` `.msg`) still require the commit-level self-host
bootstrap gates before any DONE_STRONG claim — deferred at the user's
direction on 2026-07-18, to be run before commit.
