# Investigation: submodule module-top runs BEFORE its package __init__ (module-init order, no-libpython)

## Status
active — root cause CONFIRMED + definitively bisected 2026-05-31; the fix is a
module-lifecycle rework (upfront-ordered -> lazy-at-import-site module-top
invocation), bootstrap-critical, NOT a contained /loop slice. Deferred to a
focused session. Found by probing package-import shapes after the five
contained import-machinery fixes
([[python-package-init-computed-module-attr-no-libpython]],
[[python-from-package-import-submodule-no-libpython]],
[[python-deep-dotted-package-attr-no-libpython]],
[[python-conditional-indented-import-no-libpython]],
[[python-star-import-no-libpython]]).

## Problem Description
When a package `__init__.py` imports a submodule whose module-top code has a
side effect on the package (e.g. mutating a package-level list/registry, the
common "plugin registration" pattern), the side effect is LOST under
`--backend self --python-libpython=off`, because pcc runs the SUBMODULE's
module-top BEFORE the package `__init__` module-top — the reverse of CPython,
which runs the package `__init__` first and triggers the submodule lazily when
the `from pkg import submodule` statement is reached mid-`__init__`.

## Repro
```bash
site=/tmp/p; rm -rf $site; mkdir -p $site/p
printf 'print("INIT-START")\nREG = []\nfrom p import plugin\nprint("INIT-END")\n' > $site/p/__init__.py
printf 'print("PLUGIN-RUNS")\n' > $site/p/plugin.py
printf 'import p\ndef main():\n    print("MAIN")\nmain()\n' > /tmp/m.py
PCC_PACKAGE_SITE=$site env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/m.py -o /tmp/mbin
/tmp/mbin                       # pcc:   PLUGIN-RUNS / INIT-START / INIT-END / MAIN
PYTHONPATH=$site python3 /tmp/m.py   # cpy:   INIT-START / PLUGIN-RUNS / INIT-END / MAIN
```
Side-effect symptom (the original probe):
```bash
# p/__init__.py:  REG = []\nfrom p import plugin
# p/plugin.py:    import p\np.REG.append("x")
# main:           import p; print(len(p.REG))
#   pcc -> 0   (plugin appended before REG=[] ran, then REG=[] reset it)
#   cpy -> 1
```

## Bisect (2026-05-31)
- plugin module-top RUNS at import (plugin `print` fires)  ✓
- cross-module READ works (plugin computes `Y = p.X * 10` -> 50)  ✓
- MAIN mutating `p.REG.append(...)` from the entry works (-> 2)  ✓
- SUBMODULE mutating `p.REG` during `__init__` import -> LOST (len 0)  ✗
- order probe: pcc prints `PLUGIN-RUNS, INIT-START, INIT-END`; CPython prints
  `INIT-START, PLUGIN-RUNS, INIT-END`  ✗ (definitive: submodule top runs first)
- ALSO: `p.plugin` (submodule as a package attribute) raises AttributeError
  under pcc (CPython auto-registers an imported submodule as a package attr) —
  a related sub-symptom of the same lifecycle gap.

## Root cause (source-confirmed)
The generated `main()` entry (pcc/py_frontend/codegen/module_lifecycle_lowering.py,
the `_emit_*main*` body around lines 211-228) calls ALL sibling module-tops
UPFRONT, in `self._sibling_module_inits` order, BEFORE running the entry
module's own body:
```python
for sibling_mod in self._sibling_module_inits:
    sib_top = f"_pcc_py_module_top_{sanitised_sib}"
    self.builder.call(sib_fn, [])
```
`_sibling_module_inits` is a dependency-first order
(pipeline.py `_order... "dependency-first order for sibling module top-inits"`,
~line 1996). Each module-top is idempotent (guarded by an `@.pcc.module.init.X`
seen-flag, as seen in the computed-module-top-attr IR). But because the package
`p` does `from p import plugin`, the dependency order placed `p.plugin` BEFORE
`p` — and `p.plugin` does `import p`, so `p` <-> `p.plugin` is a cycle that a
static topological order cannot linearise to match Python's semantics. CPython
does not topologically pre-order module-tops: it runs `p`'s `__init__` and, when
the `from p import plugin` statement executes, runs `p.plugin`'s top THEN
(re-entrant, guarded by `sys.modules`). pcc's upfront-ordered invocation runs
`p.plugin` first, so its mutation of `p.REG` happens before `REG = []` and is
discarded.

## Test [CONFIRMED]
The order probe above is the deterministic gate (pcc vs CPython stdout order).
No regression test added yet (the fix is deferred). A fix must make the order
probe match CPython AND the `len(p.REG)` side-effect probe return 1 AND
`p.plugin` resolve, under strict no-libpython, plus keep the full three-stage
self-host bootstrap green (the entry's module-init sequence is shared with pcc's
own ~150-module compile).

## Proposals
- No.1 Invoke a module-top at its IMPORT SITE (lazy, seen-guarded) instead of all-upfront in main()  [pending — bootstrap-critical rework]

## No.1 (design, not implemented)
Emit the call to `_pcc_py_module_top_<mod>()` at the IMPORT SITE — i.e. when an
`import mod` / `from pkg import mod` statement is lowered, call the target
module's top (guarded by the existing `@.pcc.module.init.<mod>` seen-flag) —
instead of (or in addition to) the upfront loop in `main()`. The entry's own
top-level imports would then drive the init order exactly like CPython's
`sys.modules`-guarded re-entrant import. This is bootstrap-critical: pcc's own
multi-module compile currently relies on the upfront dependency-first order, so
the change must keep stage1->stage2->stage3 byte-identity and the fallback
baselines. Likely needs: (a) call the top at each import lowering site; (b) keep
the seen-flag guard for idempotency + cycle re-entry; (c) ensure the entry
module's body (not just siblings) participates; (d) auto-register an imported
submodule as an attribute of its package object (fixes the `p.plugin`
AttributeError sub-symptom). DEFERRED — a focused module-lifecycle session, not a
/loop slice.

## Context
Found while probing pure-Python package-import shapes. The same batch confirmed
four shapes already work and were regression-locked in
`tests/python/test_native_package_alias_namespace.py` (import-as-alias,
dotted-import-as-alias, namespace package without `__init__.py`, re-export
depth). This module-init-order gap is the one real gap the batch surfaced; unlike
the five preceding import-machinery fixes (each a contained ~1-line/flag change),
its fix is a lifecycle rework and is therefore deferred and characterized here
for a focused session.
