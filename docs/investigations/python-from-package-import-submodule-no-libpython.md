# Investigation: `from package import submodule` falls back to libpython (no-libpython)

## Status
resolved 2026-05-31 — fix landed in `_top_level_import_targets`
(pcc/py_frontend/pipeline.py): also add each `from pkg import name` imported
name as a SUBMODULE discovery candidate. Probes match CPython; regression
`tests/python/test_native_package_from_import_submodule.py` (4 passed); full
three-stage self-host bootstrap green (18 passed, 4 skipped, no fallback-baseline
change). Found by probing the submodule-as-object import shape after the
computed-module-top-attr fix
([[python-package-init-computed-module-attr-no-libpython]]).

## Problem Description
`from pkg import sub`, where `sub` is a SUBMODULE file (`pkg/sub.py`) rather than
a name defined in `pkg/__init__.py`, fell back to libpython under
`--backend self --python-libpython=off` (PCC-PY-COMPILE-001 "requires libpython
fallback for multi-file compile"). This is one of the most common real-package
import shapes (`from os import path`, `from numpy import linalg`, etc.). The
dotted form `import pkg.sub` and the direct-name form `from pkg.sub import W`
already compiled+ran natively; only the submodule-as-object from-import was
missing.

## Repro
```bash
site=/tmp/p; rm -rf $site; mkdir -p $site/p
: > $site/p/__init__.py
printf 'W = 40\n' > $site/p/sub.py
printf 'from p import sub\ndef main():\n    print(sub.W)\nmain()\n' > /tmp/m.py
PCC_PACKAGE_SITE=$site env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/m.py -o /tmp/mbin
#   before: error: PCC-PY-COMPILE-001 ... requires libpython fallback for multi-file compile (modules: m)
#   after:  builds; /tmp/mbin prints 40 (== PYTHONPATH=$site python3 /tmp/m.py)
```

## Bisect (2026-05-31)
- `from p import sub` + `sub.W` (literal W) -> FALLBACK (before fix)
- `from p import sub` + `sub.W` (computed W=10*4) -> FALLBACK (before fix; same
  cause — independent of W being computed)
- `import p.sub` + `p.sub.W` -> WORKS (dotted module import)
- `from p.sub import W` -> WORKS (direct name import)
So the gap is specifically `from package import submodule` (binding the
submodule as a local name/object), not the attribute access nor the W shape.

## Root cause (source-confirmed)
The top-level import-discovery `_top_level_import_targets`
(pcc/py_frontend/pipeline.py) seeds the native multi-file compile set from the
entry module's imports:
- `import X[.Y]` lines (`_iter_source_import_specs`) -> `add_candidate("X.Y")`
  (so `import p.sub` discovers `p.sub`);
- `from X import a, b` lines (`_iter_source_import_from_specs`) -> it only did
  `add_candidate(X)` — **it never tried `X.a` / `X.b` as submodules.**

So `from p import sub` added only `p` (the package `__init__`) to the compile
set; `p.sub` was never discovered/compiled, so it was absent from
`_native_module_exports`. The from-import lowering
(`import_lowering.py::_emit_import_from`) then called
`_native_import_from_submodule("p", "sub")`, which returns `"p.sub"` only when
`"p.sub" in native_table` — it was not, so the name fell through to the
`py_cpy_import` + `py_cpy_getattr` path, tripping the no-libpython gate.

(Relative `from . import sub` already worked because it is discovered by the
separate `_package_import_targets` path used for package-internal modules;
`import p.sub` worked because `_iter_source_import_specs` yields the dotted
module name directly. Only the top-level ABSOLUTE submodule from-import was
unhandled.)

## Test [CONFIRMED]
`tests/python/test_native_package_from_import_submodule.py` — 4 passed (3.89s):
literal-attr / computed-attr / function-call submodule access + a
multi-submodule `from p import sa, sb` statement, all matching CPython under
strict no-libpython. Existing package locks green
(facade/relimport/computed-init-attr/package_import_path: 17 passed, 2 skipped).

## Proposals
- No.1 Add each `from pkg import name` imported name as a submodule discovery candidate  [CONFIRMED]

## No.1 Add each `from pkg import name` imported name as a submodule discovery candidate
### Code Change
`pcc/py_frontend/pipeline.py`, `_top_level_import_targets`: after
`add_candidate(module_spec)` for a `from module_spec import ...` statement, also
`add_candidate(module_spec + "." + imported_name)` for each imported name.
`add_candidate` resolves the module source via `_resolve_module_src_for_import`
and only adds names that resolve to a real module file on disk, so an imported
name that is a genuine function/class/constant export of the package (not a
submodule) is automatically skipped. Mirrors the dotted `import p.sub` discovery
and the relative `from . import sub` path.

```python
for module_spec, imported_names in _iter_source_import_from_specs(...):
    if module_spec.startswith("."):
        continue
    add_candidate(module_spec)
    for imported_name in imported_names:
        if imported_name and imported_name != "*":
            add_candidate(module_spec + "." + imported_name)
```

### CONFIRMED
Repro + all bisect cases now compile native and match CPython under
`--backend self --python-libpython=off`:
- `from p import sub; sub.W` (literal) -> 40;
- `from p import sub; sub.W` (computed `10*4`) -> 40;
- `from p import sub; sub.go()` -> 7;
- `from p import sa, sb; sa.A + sb.B` -> 3;
- regression `import p.sub; p.sub.W` -> 40 (unchanged).

Gates: `tests/python/test_native_package_from_import_submodule.py` 4 passed;
existing package locks 17 passed/2 skipped; **full self-host bootstrap
(the discovery closure runs over pcc's own multi-module compile):
`test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` +
`test_bootstrap_gate_baseline.py` + `test_fallback_baseline.py` +
`test_ir_py_fallback_baseline.py` — 18 passed, 4 skipped (141.84s)**, no
fallback-baseline change (the extra submodule candidates only resolve to real
files; pcc's own `from X import Y` where Y is a name still resolves the same).

## Report
Landed No.1 — a real B-P0-PKG advance: `from package import submodule`, an
extremely common real-package import shape, now compiles and runs fully native
under strict no-libpython, where it previously forced a libpython fallback. The
fix is minimal (one discovery loop), safe (only adds candidates that resolve to
real module files), consistent with the existing dotted/relative discovery, and
bootstrap-clean. Combined with the prior computed-module-top-attr fix
([[python-package-init-computed-module-attr-no-libpython]]), pure-Python package
imports now cover facade re-export, relative imports, package classes, computed
`__init__` bindings, and submodule-as-object from-imports — all native, no
libpython. The remaining package-import frontier is the C-extension ABI wall
(PCC-PKG-004), a separate major track.
