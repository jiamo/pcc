# Investigation: `from package import *` star import falls back to libpython (no-libpython)

## Status
resolved 2026-05-31 — fix landed in `_append_source_import_from_spec`
(pcc/py_frontend/pipeline.py): record the module for a `*`-only from-import so
it is discovered and compiled natively. Probes match CPython; regression
`tests/python/test_native_package_star_import.py` (3 passed); full three-stage
self-host bootstrap green (18 passed, 4 skipped, no fallback-baseline change).
Found by probing package-import shapes after
[[python-conditional-indented-import-no-libpython]].

## Problem Description
`from pkg import *` fell back to libpython under
`--backend self --python-libpython=off` ("imports still lower through CPython
fallback"). A `from pkg import name` (explicit name) worked, but the wildcard
form did not. Star import is a common real-package shape (a package `__init__`
re-exports a submodule API and user code does `from pkg import *`).

## Repro
```bash
site=/tmp/q; rm -rf $site; mkdir -p $site/q
printf 'from q.mod import foo, bar\n__all__ = ["foo","bar"]\n' > $site/q/__init__.py
printf 'def foo():\n    return 9\ndef bar():\n    return 5\n' > $site/q/mod.py
printf 'from q import *\ndef main():\n    print(foo() + bar())\nmain()\n' > /tmp/m.py
PCC_PACKAGE_SITE=$site env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/m.py -o /tmp/mbin
#   before: error PCC-PY-COMPILE-001 ... imports still lower through CPython fallback
#   after:  builds; /tmp/mbin prints 14 (== PYTHONPATH=$site python3 /tmp/m.py)
```

## Bisect (2026-05-31) — discovery-only, proven
- `from q import *` (plain) -> FALLBACK
- `import q` + `from q import *` (force `q` into the compile set first) -> 14  ✓ MATCH

So the native star BINDING already works once `q` is in the native export table;
the gap is purely that `from q import *` does not DISCOVER `q`.

## Root cause (source-confirmed)
Textual import-discovery `_append_source_import_from_spec`
(pcc/py_frontend/pipeline.py) parsed a `from MODULE import NAMES` line, filtered
`*` out of the names (`if not raw_name or raw_name == "*": continue`), and then
appended the spec only `if imported_names:`. For a `*`-only import the name list
was empty, so the whole spec was dropped — `MODULE` (`q`) was never yielded to
the discovery (`_top_level_import_targets` / `_package_import_targets`), never
added to the native compile set, and the `from q import *` statement lowered
through `py_cpy_import`, tripping the no-libpython gate. The AST-based import
lowering (`import_lowering.py::_emit_import_from` ->
`_bind_native_cross_module_imports`) already handles the `*` name by binding all
public exports of a native sibling — that path was just never reached because
`q` was absent from `_native_module_exports`.

## Test [CONFIRMED]
`tests/python/test_native_package_star_import.py` — 3 passed (3.10s):
`from q import *` with `__all__`, without `__all__` (binds all non-underscore
module globals), and binding a re-exported module-global constant — all matching
CPython under strict no-libpython.

## Proposals
- No.1 Record the module for a `*`-only from-import in discovery  [CONFIRMED]

## No.1 Record the module for a `*`-only from-import in discovery
### Code Change
`pcc/py_frontend/pipeline.py`, `_append_source_import_from_spec`: track a
`saw_star` flag and append the spec when `imported_names or saw_star`. For a
`*`-only import the recorded spec is `(module_spec, [])` — the module is
discovered (both `_top_level_import_targets` and `_package_import_targets` add
the module itself as a candidate), and the empty name list means the
submodule-candidate loops add nothing spurious. The native `*` binding is
unchanged (still done by the AST-based lowering).

```python
imported_names = []
saw_star = False
for raw_name in names_spec.split(","):
    raw_name = raw_name.strip()
    if not raw_name:
        continue
    if raw_name == "*":
        saw_star = True
        continue
    if " as " in raw_name:
        raw_name = raw_name.split(" as ", 1)[0].strip()
    imported_names.append(raw_name)
if imported_names or saw_star:
    specs.append((module_spec, imported_names))
```

### CONFIRMED
Star imports now compile native and match CPython under
`--backend self --python-libpython=off`:
- `from q import *` (with `__all__`) -> 14;
- `from r import *` (no `__all__`, binds non-underscore globals) -> 12;
- `from s import *` (re-exported constant) -> 100.

Gates: `tests/python/test_native_package_star_import.py` 3 passed;
**full self-host bootstrap (discovery runs over pcc's own compile, which uses
`from .mod import *` re-exports):
`test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` +
`test_bootstrap_gate_baseline.py` + `test_fallback_baseline.py` +
`test_ir_py_fallback_baseline.py` — 18 passed, 4 skipped (156.69s)**, no
fallback-baseline change.

## Report
Landed No.1 — fifth consecutive contained B-P0-PKG import-machinery fix this
session (after [[python-package-init-computed-module-attr-no-libpython]],
[[python-from-package-import-submodule-no-libpython]],
[[python-deep-dotted-package-attr-no-libpython]],
[[python-conditional-indented-import-no-libpython]]). `from package import *`
now compiles and runs fully native under strict no-libpython. The fix is minimal
(a `saw_star` flag in the textual discovery), exploits the already-working native
star binding, and is bootstrap-clean. pure-Python package imports now cover
facade re-export, relative imports, package classes, computed `__init__`
bindings, submodule-as-object from-imports, deep dotted access, conditional /
lazy entry imports, and wildcard imports — all native, no libpython.
