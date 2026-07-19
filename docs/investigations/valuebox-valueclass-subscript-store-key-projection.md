# Investigation: valueclass constructor subscript-store keys bypass object projection (identity instance + unhashable TypeError)

## Status
resolved

## Problem Description

V2 escape-boundary slice continuation (predecessors:
`valuebox-valueclass-membership-needle-projection.md` for the getitem-key /
membership side, `valuebox-valueclass-builtin-object-boundary-projection.md`
for `hash/repr/str/type/format`). The subscript **load** side already routes
direct valueclass constructor keys through
`subscript_lowering._emit_subscript_key_object(...)`, which intercepts a
direct constructor via `_maybe_emit_valueclass_constructor_payload(...)` and
boxes the payload with `py_valuebox_new`. The subscript **store** side does
not: `_emit_subscript_store_value(...)` emits dict and generic-object setitem
keys with plain `_emit_as_object(...)`, which lowers a direct
`Segment(Point(...), Point(...))` constructor to the legacy identity-instance
ctor (`user_value_mod_Segment___init__`).

Result: `table[Segment(...)] = 10` stores under an identity instance whose
runtime hash path raises `TypeError: unhashable type`, the store-site error is
deferred, and the projected getitem `table[Segment(...)]` then raises
`KeyError`. CPython oracle semantics for a value-semantic key class expect the
store/lookup pair to hit by value.

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_subscript_store_key_projection_boxes_valuebox' \
  'tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_subscript_store_key_projection_self_backend' \
  -q -n0
```

Observed red (2026-06-10):

- IR guard: `@user_value_mod_put` contains `py_obj_setitem` but no
  `py_valuebox_new`; `@main`'s dict store key likewise reaches
  `user_value_mod_Segment___init__` (getitem keys in `main` are already
  projected by the predecessor slice, so `py_valuebox_new` appears in `main`
  only for loads).
- Strict self-backend runtime probe (`--backend self --python-libpython=off
  --ir-scaffold=on`): exits 1 with chained
  `TypeError: unhashable type` (twice) followed by `KeyError` at the first
  `print(table[Segment(...)])`.

Type-inference note: `holder: Any = table` and `outer[0]` (elem of
`[table]`) are both flow-narrowed to `DictType`, so the generic
`py_obj_setitem` branch is only reachable through a genuinely dynamic
receiver such as an `Any` formal parameter (`def put(d: Any)`), which is what
the regressions use.

## Test [CONFIRMED]

- `tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_subscript_store_key_projection_boxes_valuebox`
  — red observed under the command above (missing `py_valuebox_new` in
  `@user_value_mod_put`).
- `tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_subscript_store_key_projection_self_backend`
  — red observed under the command above (probe exit 1, chained
  `TypeError: unhashable type` → `KeyError`).

## Proposals
- No.1 Route subscript-store dict/object keys through `_emit_subscript_key_object(...)`   [CONFIRMED]

## No.1 Route subscript-store dict/object keys through `_emit_subscript_key_object(...)`

### Code Change

In `pcc/py_frontend/codegen/subscript_lowering.py::_emit_subscript_store_value`,
replace `self._emit_as_object(idx_expr)` with
`self._emit_subscript_key_object(idx_expr)` for the `DictType` `py_dict_set`
branch and the trailing generic `py_obj_setitem` branch — the exact helper the
load side already uses, keeping store/load key projection symmetric.

Known sibling gaps deliberately **not** in this slice (same boundary family,
separate syntactic entries, each needs its own red/green pair):

- unpack-target subscript store keys:
  `assignment_store_lowering.py::_store_value_at_subscript` marshals the key
  with plain `marshal_to_object`.
- augmented subscript assignment keys (`d[Segment(...)] += 1`):
  `assignment_statement_lowering.py` aug-assign subscript path emits the key
  with plain `_emit_expr` + marshal.
- dict-literal constructor keys (`{Segment(...): v}`) in
  `literal_lowering.py` if still unprojected.
- weak-dict store keys (`py_weak_value_dict_set` / `py_weak_key_dict_set`)
  stay un-projected on both load and store sides; valueclass keys for weak
  dicts are an identity-semantics question, not a projection bug.

### CONFIRMED

Observed gate results after the two-site change (2026-06-10, this host):

- Focused IR guard + focused strict self-backend runtime regression (repro
  command above) -> 2 passed in 33.78s; the runtime probe prints
  `10 / 20 / True / 2 / 11 / 2`, matching CPython value-semantic keys.
- Full `tests/python/test_py_value_class_unboxed.py -q -n0` -> 34 passed in
  11.00s.
- Full `tests/python/data_model/test_value_class_runtime.py -q -n0` (run as
  the `not nested_valuebox` / `nested_valuebox` halves) -> 14 + 22 = 36
  passed (8.37s + 19.57s).
- V0/V1/status batch (`test_py_value_class_unboxed.py`,
  `test_value_class_source_shape.py`, `test_value_class_field_flattening.py`,
  `test_value_model_valhalla.py` `-q -n0`) -> 51 passed in 6.73s.
- Full GC common contract `tests/python/gc_production_contract -q -n0` ->
  130 passed in 26.68s.
- Fallback/no-libpython baselines (`test_fallback_baseline.py`,
  `test_ir_py_fallback_baseline.py` `-q -n0`) -> 18 passed in 119.91s.
- Touched-file `py_compile` passed; `git diff --check` passed; residual
  `pgrep` for pcc/pytest/bootstrap children empty.
- Mandatory full five-GC self bootstrap matrix
  `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py -q -n0` -> 5 passed in
  469.21s (fresh stage2/stage3 per backend, normalized `pcc2 == pcc3` checked
  by the gate).

## Report

Proposal No.1 landed: `_emit_subscript_store_value(...)` now emits the
`DictType` `py_dict_set` key and the trailing generic `py_obj_setitem` key
through `_emit_subscript_key_object(...)`, the same projection helper the
subscript load side already uses, so store/load key projection is symmetric.
The slice is bootstrap-verified `DONE_WEAK`. It does not prove the sibling
gaps listed under No.1 (unpack-target store keys, aug-assign subscript keys,
dict-literal constructor keys, weak-dict key policy), recursive valueclasses,
complete V2 marshal coverage, flattened storage, typed arrays, or total-goal
completion.
