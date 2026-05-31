# Investigation: deep dotted package attribute access `a.b.c.X` raises AttributeError (no-libpython)

## Status
resolved 2026-05-31 — fix landed in `_native_module_expr_export_info`
(pcc/py_frontend/codegen/native_modules.py): flatten a 3+-level Name/Attr module
chain to its spelled dotted name and resolve it in the native export table,
gated on the root being a known native module alias. Probes match CPython;
regression `tests/python/test_native_package_deep_dotted_attr.py` (4 passed);
full three-stage self-host bootstrap green (18 passed, 4 skipped, no
fallback-baseline change). Found by probing package-import shapes after
[[python-from-package-import-submodule-no-libpython]].

## Problem Description
`import a.b.c; a.b.c.X` (a 3+-level dotted package access) COMPILED fine but
RAN with `AttributeError: b` (the intermediate `a.b` access) under
`--backend self --python-libpython=off`, while CPython prints the value.
2-level access (`import a.b; a.b.X`, `import a.sub; a.sub.X`,
`import pkgx.pathy; pkgx.pathy.join(...)`) already worked. So the gap is the
attribute-resolution depth, not import discovery (the modules compile) and not
the package shape.

## Repro
```bash
site=/tmp/a; rm -rf $site; mkdir -p $site/a/b/c
: > $site/a/__init__.py; : > $site/a/b/__init__.py
printf 'X = 13\n' > $site/a/b/c/__init__.py
printf 'import a.b.c\ndef main():\n    print(a.b.c.X)\nmain()\n' > /tmp/m.py
PCC_PACKAGE_SITE=$site env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/m.py -o /tmp/mbin
/tmp/mbin            # before: Traceback ... AttributeError: b   (after: 13)
PYTHONPATH=$site python3 /tmp/m.py   # 13
```

## Bisect (2026-05-31)
- `import a.b; a.b.X` (2-level pkg) -> 11  ✓ MATCH
- `import a.sub; a.sub.X` (2-level submodule) -> 12  ✓ MATCH
- `import pkgx.pathy; pkgx.pathy.join("x","y")` (2-level fn) -> x/y  ✓ MATCH
- `import a.b.c; a.b.c.X` (3-level) -> AttributeError b  ✗ DIFF (cpy 13)
- `import a.b.c.leaf; a.b.c.leaf.X` (4-level submodule) -> AttributeError b  ✗ DIFF (cpy 99)
So 2-level works, 3+-level fails at the intermediate `a.b`.

## Root cause (source-confirmed)
`a.b.c.X` parses to `Attr(obj=Attr(obj=Attr(obj=Name('a'), name='b'), name='c'), name='X')`.
Attr lowering (attr_load_lowering.py) calls
`_native_module_object_export_info(expr.obj, expr.name)` =
`_native_module_expr_export_info(<a.b.c>, "X")`. That function only had two
shapes:
- `module_expr` is a `Name` (one-level `mod.attr`);
- `module_expr` is an `Attr` **whose `.obj` is a `Name`** (two-level
  `pkg.sub.attr`, e.g. `urllib.parse.quote`).

For `a.b.c.X` the module expression is `a.b.c`, an `Attr` whose `.obj` is itself
an `Attr` (`a.b`), so neither branch matched and the function returned None. The
access then fell through to the generic runtime `py_obj_getattr` chain, which
evaluated `a.b` as a getattr on the `a` module object — and the package module
object has no `b` attribute in pcc's model — raising `AttributeError: b`.

## Test [CONFIRMED]
`tests/python/test_native_package_deep_dotted_attr.py` — 4 passed (3.93s):
3-level constant, 3-level function call, 4-level submodule, and a regression that
a real object attribute chain (`o.n.v`) is NOT misresolved as module access.

## Proposals
- No.1 Flatten the dotted module chain and resolve the spelled name in the native export table  [CONFIRMED]

## No.1 Flatten the dotted module chain and resolve the spelled name in the native export table
### Code Change
`pcc/py_frontend/codegen/native_modules.py`: add a deep-chain branch to
`_native_module_expr_export_info` (after the existing one-level branch) plus a
`_dotted_module_parts` helper. The helper flattens a pure `Attr`/`Name` chain
(`a.b.c`) to `["a", "b", "c"]`, returning None for any chain containing a call /
subscript (so non-module attribute chains are not mistaken for dotted module
access). The new branch, when the chain has length >= 2 and its root is a known
native module alias, looks up the spelled dotted name (`"a.b.c"`) directly in
`_native_module_exports[spelled][attr_name]`.

```python
parts = self._dotted_module_parts(module_expr)
if parts is not None and len(parts) >= 2:
    if parts[0] in self._native_module_aliases:
        native_table = self._native_module_exports
        if native_table is not None:
            spelled = ".".join(parts)
            info = native_table.get(spelled, {}).get(attr_name)
            if info is not None:
                return spelled, info
return None
```

### CONFIRMED
Repro + all bisect cases now match CPython under
`--backend self --python-libpython=off`:
- `import a.b.c; a.b.c.X` -> 13 (was AttributeError);
- `import a.b.c.leaf; a.b.c.leaf.X` -> 99;
- `import a.b.c; a.b.c.fn()` -> 77;
- regression `import a.b; a.b.X` -> 11 (unchanged);
- regression `from p.m import M; M().n.v` -> 5 (real object attr chain NOT
  misresolved — the alias-root gate guards this).

Gates: `tests/python/test_native_package_deep_dotted_attr.py` 4 passed;
**full self-host bootstrap (the resolution runs over pcc's own deeply-dotted
module access such as `pcc.py_frontend.codegen.*`):
`test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` +
`test_bootstrap_gate_baseline.py` + `test_fallback_baseline.py` +
`test_ir_py_fallback_baseline.py` — 18 passed, 4 skipped (155.96s)**, no
fallback-baseline change.

## Report
Landed No.1 — third consecutive contained B-P0-PKG import-machinery fix this
session (after [[python-package-init-computed-module-attr-no-libpython]] and
[[python-from-package-import-submodule-no-libpython]]). `import a.b.c; a.b.c.X`
deep dotted package access now resolves natively. The fix is minimal (one branch
+ a small pure-AST helper), safe (the alias-root gate keeps real object
attribute chains on `py_obj_getattr`), and bootstrap-clean.

## Other gaps surfaced while probing (deferred, documented for later iterations)
The same probe batch surfaced two more pure-Python package-import gaps that did
NOT get fixed here (separate, more involved emission paths):
- `from p import *` (star import driven by `__all__`) -> libpython FALLBACK;
- a conditional/indented import (`try: from p import real as m\nexcept
  ImportError: ...`) -> libpython FALLBACK. The entry import-discovery
  (`_top_level_import_targets`) uses `top_level_only=True`, so an indented
  import inside a `try` block is not discovered for native compile. These are
  candidates for a future investigation.
