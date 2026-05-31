# Investigation: class accessed via dotted module path `import p.sub; p.sub.Thing(...)` fails (no-libpython)

## Status
active — root cause CONFIRMED + bisected 2026-05-31; a speculative fix was
attempted and REVERTED (it touched the wrong path and the gap spans multiple
paths). NOT a contained /loop slice — deferred. Found by probing package-import
shapes after the five contained import-machinery fixes
([[python-star-import-no-libpython]] etc.).

## Problem Description
A CLASS accessed through a DOTTED module path — `import p.sub` then
`p.sub.Thing(...)` — fails under `--backend self --python-libpython=off`, while
a function (`p.sub.func()`) or a module global (`p.sub.GLOB`) through the same
dotted path works, AND the same class via the from-import alias
(`from p import sub; sub.Thing(...)`) works. So the gap is specifically a class
reached via `<pkg>.<sub>.<Class>` dotted access.

## Repro
```bash
site=/tmp/p; rm -rf $site; mkdir -p $site/p; : > $site/p/__init__.py
printf 'class Thing:\n    def __init__(self, v):\n        self.v = v\n    def doubled(self):\n        return self.v * 2\n' > $site/p/sub.py
printf 'import p.sub\ndef main():\n    print(p.sub.Thing(3).doubled())\nmain()\n' > /tmp/m.py
PCC_PACKAGE_SITE=$site env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/m.py -o /tmp/mbin
/tmp/mbin                       # pcc: Traceback ... AttributeError: sub   (cpy: 6)
```

## Bisect (2026-05-31)
- `import p.sub; p.sub.GLOB` (module global, dotted) -> 7  ✓ MATCH
- `import p.sub; p.sub.func()` (function, dotted) -> 99  ✓ MATCH
- `import p.sub; T = p.sub.Thing; T(3).doubled()` (class LOAD then call) -> TypeError "unsupported operand for *" (instance `.v` not set)  ✗
- `import p.sub; p.sub.Thing(3).doubled()` (class direct CALL) -> AttributeError: sub  ✗
- `from p import sub; sub.Thing(3).doubled()` (class via from-import ALIAS) -> 6  ✓ MATCH
- 3-level `import a.b.c.leaf; a.b.c.leaf.Q(5).sq()` -> AttributeError: b  ✗

## Root cause (source-confirmed) — MULTIPLE paths, hence not contained
1. CLASS LOAD path: `p.sub.Thing` as a load goes through
   `_emit_attr` -> `_native_module_object_export_info` ->
   `_emit_native_module_export_value` (native_modules.py:~1493). Its
   `kind=="class"` branch DOES pass `field_names` / `methods`, but via
   `self.class_lowering.declare_extern_class(...)` — a DIFFERENT extern-class
   declaration than the FROM-IMPORT alias path, which uses
   `_ensure_native_module_alias_class_export` ->
   `_declare_native_module_extern_class(field_names, methods, base_names, ...)`.
   The `declare_extern_class` result constructs an instance whose `.v` is unset
   (TypeError on `self.v * 2`), while the alias path's
   `_declare_native_module_extern_class` constructs correctly — which is why
   `from p import sub; sub.Thing(...)` works. The precise reason the two declare
   methods diverge on construction is UNCONFIRMED (a route-swap to the
   field-aware helper is speculative without it) — needs deeper investigation,
   not a blind swap.
2. CLASS CALL path: `p.sub.Thing(3)` is a Call whose callee is the dotted Attr
   `p.sub.Thing`; the call lowering resolves the callee on a DIFFERENT path that
   does not reach the native module-export resolution at all, so it falls to
   `py_obj_getattr(p, "sub")` and raises `AttributeError: sub` (the package
   module object has no `sub` attribute in pcc's model).
A correct fix must (a) route the dotted-module class LOAD through the
field-aware `_declare_native_module_extern_class` (the alias path's helper), AND
(b) make the call lowering resolve a dotted-module class callee — two distinct
shared-codegen paths. (The attr-lowering one-level `kind=="class"` branch also
only handles `expr.obj` being a `Name`, so it never fires for the dotted case.)

## Attempted + REVERTED (2026-05-31)
Extracted `_ensure_native_module_class_export_from_info` from
`_ensure_native_module_alias_class_export` and wired the attr-lowering
`kind=="class"` branch's dotted case through it. This had NO effect on the
repro: the active path for a dotted class load is
`_emit_native_module_export_value` (reached via `_native_module_object_export_info`
BEFORE the alias-export branch), not the alias-export branch that was edited; and
it does not address the CALL path at all. Reverted per AGENTS.md §9
(no unverified edits in shared codegen) — native_modules.py / attr_load_lowering.py
restored. The gap needs a coordinated multi-path fix, a focused session.

## Test [CONFIRMED failing]
The repro above (`AttributeError: sub`) and the LOAD variant (`TypeError`) are
the deterministic gates. No regression test added (fix deferred). A fix must make
`p.sub.Thing(3).doubled()` -> 6, the LOAD variant -> 6, the 3-level variant work,
keep `from p import sub; sub.Thing(...)` working, and keep the full self-host
bootstrap green.

## Proposals
- No.1 Route dotted-module class load through the field-aware extern-class declaration AND resolve dotted-module class callees in the call lowering  [pending — multi-path, deferred]

## Context
Found while probing pure-Python package-import shapes. The same batch confirmed
`from p import sub as s` and `from p import compute as c` (aliased from-imports)
already work (locked in test_native_package_alias_namespace.py). Two further
gaps surfaced in the same batch and are noted for later: a conditional import
INSIDE a package `__init__` (`if cond: from p.fast import impl`) falls back —
the discovered-module analogue of the entry-only fix in
[[python-conditional-indented-import-no-libpython]]; and module-level
`__getattr__` (PEP 562) is not honored (`p.DYNAMIC` -> AttributeError). All three
are non-contained (multi-path / blast-radius / feature) and deferred.
