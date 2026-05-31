# Investigation: conditional / indented imports in the entry module fall back to libpython (no-libpython)

## Status
resolved 2026-05-31 (present-dependency case) — fix landed in
`_collect_multi_source_relative_closure` (pcc/py_frontend/pipeline.py): scan the
ENTRY module's imports including indented ones. Probes match CPython; regression
`tests/python/test_native_package_conditional_import.py` (4 passed); full
three-stage self-host bootstrap green (18 passed, 4 skipped, no fallback-baseline
change). The ABSENT-optional-dependency sub-case (`try: import missing`) is a
separate, more involved follow-up (native ImportError emission for unresolvable
imports under =off) — documented below, not fixed here. Found by probing
package-import shapes after [[python-deep-dotted-package-attr-no-libpython]].

## Problem Description
An import that is INDENTED in the entry module — inside a module-level `try:`
/ `if:` block, or inside a function (lazy import) — fell back to libpython under
`--backend self --python-libpython=off` ("imports still lower through CPython
fallback; generated IR still calls py_cpy_* helpers"). A top-level
`from p import real as m` worked; the same statement indented under `try:` did
not. This is one of the most common real-world import shapes (optional
dependencies: `try: import fast except ImportError: import slow`; lazy imports
to break import cycles).

## Repro
```bash
site=/tmp/p; rm -rf $site; mkdir -p $site/p; : > $site/p/__init__.py
printf 'Z = 42\n' > $site/p/real.py
printf 'try:\n    from p import real as m\nexcept ImportError:\n    m = None\ndef main():\n    print(m.Z)\nmain()\n' > /tmp/m.py
PCC_PACKAGE_SITE=$site env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/m.py -o /tmp/mbin
#   before: error PCC-PY-COMPILE-001 ... imports still lower through CPython fallback
#   after:  builds; /tmp/mbin prints 42 (== PYTHONPATH=$site python3 /tmp/m.py)
```

## Bisect (2026-05-31)
- top-level `from p import real as m; m.Z` -> 42  ✓ MATCH
- `try:`-indented `from p import real as m` -> FALLBACK  ✗
- `if True:`-indented `from p import real as m` -> FALLBACK  ✗
- function-level `def main(): from p import real as m` -> FALLBACK  ✗
- `try: import nonexistent_optional_xyz` (ABSENT dep) -> FALLBACK  ✗ (separate sub-case)
So every INDENTED import fell back; only top-level imports were discovered.

## Root cause (source-confirmed)
The entry import-discovery `_top_level_import_targets`
(pcc/py_frontend/pipeline.py) scans the entry source TEXTUALLY for imports, but
its `_iter_source_import*` helpers skip indented lines when
`top_level_only=True` (`if top_level_only and raw_line[:1].isspace(): continue`).
The entry closure (`_collect_multi_source_relative_closure`, the no-dot-entry
branch) called it with `top_level_only=True`, so any indented import — inside a
module-level `try:` / `if:` block, or inside a function — was never discovered,
the referenced module was never added to the native compile set, and the
import lowered through `py_cpy_import`, tripping the no-libpython gate.

## Test [CONFIRMED]
`tests/python/test_native_package_conditional_import.py` — 4 passed (3.99s):
try-import, if-block import, function-local import, and try-import of a
submodule object — all present-dependency, all matching CPython under strict
no-libpython.

## Proposals
- No.1 Scan the entry module's imports including indented ones  [CONFIRMED]
- No.2 Emit native ImportError for unresolvable imports under =off (absent optional dep)  [pending — follow-up]

## No.1 Scan the entry module's imports including indented ones
### Code Change
`pcc/py_frontend/pipeline.py`, `_collect_multi_source_relative_closure`
(no-dot-entry branch): pass `top_level_only=(not is_entry)` to
`_top_level_import_targets`, where `is_entry = mod_name == entry_mod`. So the
ENTRY module's imports are scanned including indented ones; discovered (non-entry)
modules keep `top_level_only=True` to bound the closure and the self-host blast
radius. `add_candidate` only adds names that resolve to a real module file under
the source root, so a missing / optional C-extension import inside `try:` is
still left to `py_cpy_import` (it does not get spuriously pulled in).

### CONFIRMED
Present-dependency indented imports now compile native and match CPython under
`--backend self --python-libpython=off`:
- `try: from p import real as m; m.Z` -> 42;
- `if True: from p import real as m; m.Z` -> 42;
- `def main(): from p import real as m; m.Z` -> 42;
- `try: from p import real; real.Z + 1` -> 43.

Gates: `tests/python/test_native_package_conditional_import.py` 4 passed;
**full self-host bootstrap (the entry closure runs over pcc's own compile):
`test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` +
`test_bootstrap_gate_baseline.py` + `test_fallback_baseline.py` +
`test_ir_py_fallback_baseline.py` — 18 passed, 4 skipped (151.31s)**, no
fallback-baseline change (the entry's indented imports were already reachable or
resolve fine; discovered modules unchanged).

## No.2 Emit native ImportError for unresolvable imports under =off (follow-up)
The ABSENT-optional-dependency case — `try: import missing except ImportError:
m = None` where `missing` has no resolvable source — still falls back: an
unresolvable absolute import lowers to `py_cpy_import`, which the no-libpython
gate rejects at compile time even though at runtime the import would raise
ImportError that the `except` handles. A native fix would emit a
`raise ImportError("No module named 'missing'")` stub for an unresolvable
absolute import under `--python-libpython=off`, so the `try/except` catches it
natively. This touches import lowering + the gate's notion of "fallback" (a
native raise is not a `py_cpy_*` call) and so is a separate, more involved change
— deferred. My No.1 fix does not regress this case (it failed before and fails
the same way after).

## Report
Landed No.1 — fourth consecutive contained B-P0-PKG import-machinery fix this
session (after [[python-package-init-computed-module-attr-no-libpython]],
[[python-from-package-import-submodule-no-libpython]],
[[python-deep-dotted-package-attr-no-libpython]]). Conditional / indented /
lazy imports of a PRESENT dependency in the entry module now compile and run
fully native under strict no-libpython. The fix is minimal (one `is_entry`
flag), safe (the resolvability guard keeps absent/optional C-ext imports out;
discovered modules unchanged), and bootstrap-clean. The absent-optional-dep
sub-case is documented as No.2 for a future session.
