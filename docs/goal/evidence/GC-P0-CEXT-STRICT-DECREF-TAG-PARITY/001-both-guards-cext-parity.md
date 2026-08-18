# GC-P0-CEXT-STRICT-DECREF-TAG-PARITY: strict incref+decref accept registry-proven C-ext tags

## Root cause (confirmed)

The strict pcc-Python `_py_incref_prepare` AND `_py_decref_prepare`
(pcc/py_runtime/py/py_obj.py) both reject any `tag > 500` with a plain
tag-range check before touching the refcount. Dynamic C-extension objects use
registry tags above the builtin range (probe tag = 65536), so a strict
`py_incref` could NOT retain such an object (a container retain left the
refcount at 1) and the terminal `py_decref` never ran the deallocator. The C
runtime (`py_obj.c`) already gates the same rejection on
`pcc_capi_is_cext_type_tag(tag) == 0`, so it accepts registry-proven tags and
routes the terminal release to `pcc_capi_dealloc_cext_object`.

The prior investigation's No.1 (mirror the exemption in DECREF only) was
DENIED with the reasoning "object not managed/known, append did not retain
(1->1)". The measured cause is narrower: No.1 only touched decref, so
`_py_incref_prepare` still rejected the tag and the container retain still
failed. Fixing BOTH guards achieves full C parity with no GC-index
"managed/known" registration -- matching the C runtime, which also does not
GC-track C-extension objects and drives their lifecycle by tag + refcount.

## Fix (port-only, mirrors the already-correct C)

In `_py_incref_prepare` and `_py_decref_prepare`, gate the `> 500` rejection on
the registry, exactly as the C owner does:

```python
or (tag > 500 and pcc_capi_is_cext_type_tag(tag) == 0)
```

The registry `pcc_capi_is_cext_type_tag` extern is declared in py_obj.py and
remains the single acceptance authority (unmanaged/unknown high tags return 0
and stay fail-closed). The call is short-circuited behind `tag > 500`, so the
hot path for ordinary objects (tag <= 500) is unchanged. The dealloc
dispatcher (py_obj_dealloc.py) already routed registry-proven tags to
`pcc_capi_dealloc_cext_object`, and list retain (`py_list_append` ->
`pcc_gc_store_ptr`) uses the same incref path, so no other change was needed.

## Gates (all green)

Test-first differential `tests/python/test_cext_strict_decref_lifecycle.py`
(C vs strict pcc-Python), 5 passed:
- direct refcount lifecycle [c]+[pcc_python]: PyType_GenericNew -> 1, py_incref
  (container retain) -> 2, py_decref (caller release) -> 1, py_decref (terminal)
  -> 0 with tp_dealloc invoked exactly once. Before the fix the [pcc_python]
  arm failed at the retain step (refcount stayed 1); the [c] arm always passed.
- terminal list split-store [c]+[pcc_python]: py_list_append retains -> 2,
  caller py_decref -> 1, py_decref(list) split-store release runs the
  deallocator exactly once.
- parity/negative source contract: both prepare guards accept only
  registry-proven high tags; no blanket ">500: return" survives.

Regression / closures:
- `tests/python/test_gc_update_referents.py`: 35 passed (visit/dealloc/cext
  contracts unregressed).
- self-backend no-libpython closures OK: py_obj.py, py_list.py,
  py_capi_cext_runtime.py, py_capi_type_runtime.py.
- `scripts/goal_state.py validate`: OK, 469 tasks.

## Exit criteria

1. Registry-proven dynamic C-ext objects are recognized by the strict
   ownership protocol from allocation (refcount 1) through retain/release --
   proven operationally equal to the C runtime by the differential.
2. Container retain, caller release and terminal split-store release produce
   exact 1->2->1->0 and invoke tp_dealloc exactly once -- both differentials.
3. Strict incref/decref accept only registry-proven tags; unmanaged/unknown
   high tags stay fail-closed (structural `and is_cext == 0`, pinned by the
   parity/negative contract).
