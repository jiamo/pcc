# 001 — generic getattr result made uniformly owned and emitter-noted

Date: 2026-09-02

## Claim boundary

Fixes the leak on the GENERIC builtin-getattr lowering path only (the
non-native-module, non-cpython branch of `_emit_getattr_builtin`).  The
native-module getattr edges and the cpython branch are NOT changed and their
audit stays open (see below).

## Runtime helpers read (not guessed)

`py_obj_getattr` / `py_obj_getattr_maybe` (C `py_obj_ops_dispatch.c`, mirrored
in the port) return a NEW owned reference on every non-NULL edge:

```text
__class__            -> py_type_builtin -> pcc_builtin_type_class: py_incref(*slot)
instance attr        -> py_instance_getattr(_default): every return increfs (cls_obj,
                        dyn_obj, stored value, or fabricates a bound method)
class attr           -> py_class_getattr (owned)
bound builtin method -> py_builtin_pop_bound: fabricates a fresh object
cext                 -> pcc_capi_cext_object_getattr (owned)
__getattribute__/__getattr__ -> class_call_binary_method (Python call result, owned)
```

## Code change

`_emit_getattr_builtin` (generic path):
- 2-arg: the `py_obj_getattr` result is recorded owned via
  `_note_owned_object_value(got)`, so a discarded/rebound `getattr(o, 'x')`
  releases it exactly once.  The AST classifier `_expr_returns_owned_object`
  is deliberately NOT flipped — the native-module and cpython getattr branches
  (which return earlier) keep their own unaudited ownership and must not be
  over-released.
- 3-arg: both phi edges are made to own exactly one reference.  The present
  edge releases the already-evaluated, unused default when it was an owned
  temporary; the missing edge retains a borrowed default (or transfers an
  owned temporary) so the result is owned.  The phi is noted owned.

The local-assignment path already consults `_value_is_owned_object` (the
noted-owned set), so a local bound to the result takes ownership and releases
it on rebind/None.

## Evidence (all GC0/3/4, host-built binaries)

Function-scope differential (`getattr_local.py`), byte-identical to CPython on
refcount(0) / generational(3) / relocating(4): 2-arg hit bare-discard (shared
attr NOT over-released), 2-arg rebind, 3-arg hit (owned-temp default released
on the present edge), 3-arg miss (owned-temp default becomes the owned
result), 3-arg miss with a borrowed local default (result does not free it).

Fabricated-bound-method RSS loop (`getattr(lst, 'pop')` x 4,000,000): max RSS
3.1 MB (bounded); before the fix each fabricated bound method leaked (the
sibling hasattr leak test caps the same shape at 160 MB and notes >200 MB
unfixed).

Tests added to `tests/python/test_hasattr_getattr_probe.py`:
`test_getattr_result_ownership_matches_cpython_on_gc0_3_4` and
`test_getattr_pop_fabrication_result_is_released`.  Full file: 4 passed,
1 xfailed (the pre-existing try/except-AttributeError escape, unrelated).

Closure: ON-mode closed-world (what stage1/pcc1 uses) compiles this module
fine (the v10/v11/v12 pcc1 builds all succeeded and compiled class_gen); the
change adds no new imports and only helpers already in the host contract
(`_note_owned_object_value`, `_gc_retain`, `_gc_release`,
`_release_context_label`, `_expr_returns_owned_object`) plus runtime calls
(`pcc_gc_retain`/`pcc_gc_release`/`py_clear_exception`) already used in this
file.  `builtin_type_attr_lowering` is not a tracked per-module fallback
module; the OFF-mode standalone probe grew +67 raw py_cpy purely because the
whole `_emit_getattr_builtin` function is OFF-mode cpython fallback (425 at
HEAD) and my added lines fall the same way — no ON-mode / self-host effect.

## Open boundary (honest)

- The native-module getattr branch (`_maybe_emit_native_module_getattr`:
  `py_module_attr_get` value, dynamic-name select, `_emit_native_builtin_module_attr`,
  `_emit_native_module_export_value`, the cpython 3-arg default) has mixed
  owned/borrowed edges that were NOT audited or changed; flipping the AST
  classifier for all builtin getattr is still blocked on that audit.
- A separate pre-existing limitation: a module-GLOBAL bound to a noted-owned
  RHS is not released on reassignment (`freed default2` missing in a
  module-scope probe), because the module-global store path does not consult
  the noted-owned set the way the local path does.  This is the
  module-global-store subsystem, not the getattr result, and is out of this
  fix's scope.
