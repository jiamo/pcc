# Investigation: valueclass constructor unpack-target subscript-store keys bypass object projection

## Status
resolved

## Problem Description

Direct successor of
`valuebox-valueclass-subscript-store-key-projection.md` (plain subscript
stores), which enumerated this site as a known sibling gap. Tuple-unpack
assignment targets that are subscripts go through
`assignment_store_lowering.py::_store_value_at_subscript(...)`, which emits
the key with plain `_emit_expr` + `marshal_to_object` and `py_obj_setitem`.
A direct valueclass constructor key in an unpack target —
`table[Segment(Point(1, 2), Point(3, 4))], extra = 30, 1` — therefore
materializes as a legacy identity instance
(`user_value_mod_Segment___init__`) instead of a boxed valuebox, while the
subscript load side projects the same constructor through
`_emit_subscript_key_object(...)`. Runtime consequence is identical to the
predecessor: the identity-instance key raises `TypeError: unhashable type`
at the store, the error is deferred, and the projected getitem raises
`KeyError`.

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_unpack_subscript_store_key_projection_boxes_valuebox' \
  'tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_unpack_subscript_store_key_projection_self_backend' \
  -q -n0
```

Observed red (2026-06-10): IR guard fails with
`call ... @user_value_mod_Segment___init__` present in `@main`; the strict
self-backend probe (`--backend self --python-libpython=off
--ir-scaffold=on`) exits 1 with chained `TypeError: unhashable type` then
`KeyError` at the first `print(table[Segment(...)])`.

## Test [CONFIRMED]

- `tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_unpack_subscript_store_key_projection_boxes_valuebox`
  — red observed under the command above.
- `tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_unpack_subscript_store_key_projection_self_backend`
  — red observed under the command above (probe exit 1).

## Proposals
- No.1 Route `_store_value_at_subscript(...)` keys through `_emit_subscript_key_object(...)`   [CONFIRMED]

## No.1 Route `_store_value_at_subscript(...)` keys through `_emit_subscript_key_object(...)`

### Code Change

In `pcc/py_frontend/codegen/assignment_store_lowering.py::_store_value_at_subscript`,
replace the `idx_val = self._emit_expr(target.idx)` +
`marshal.marshal_to_object(..., idx_val, target.idx.ty)` key emission with
`k_obj = self._emit_subscript_key_object(target.idx)` — the same projection
helper used by the subscript load side and by the plain subscript-store fix
in the predecessor slice. Evaluation order is preserved: the stored value is
pre-computed by the caller before this helper runs; the key is still emitted
after the receiver object.

Remaining enumerated siblings (not this slice): aug-assign subscript keys
(`d[Segment(...)] += 1`), dict-literal constructor keys
(`{Segment(...): v}`), weak-dict store-key policy.

### CONFIRMED

Observed gate results after the one-site change (2026-06-10, this host):

- Focused IR guard + strict self-backend runtime regression (repro command
  above) -> 2 passed in 30.41s; probe prints `30 / 1 / True / 1`.
- Full `tests/python/test_py_value_class_unboxed.py -q -n0` -> 35 passed.
- Full `tests/python/data_model/test_value_class_runtime.py -q -n0`
  (`not nested_valuebox` / `nested_valuebox` halves) -> 14 + 23 = 37 passed.
- V0/V1/status batch -> 52 passed.
- Full GC common contract `tests/python/gc_production_contract -q -n0` ->
  130 passed.
- Fallback/no-libpython baselines -> 18 passed.
- Touched-file `py_compile` passed; `git diff --check` passed; residual
  process check empty.
- Mandatory full five-GC self bootstrap matrix
  `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py -q -n0` -> 5 passed
  in 375.59s (fresh stage2/stage3 per backend, normalized `pcc2 == pcc3`).

## Report

Proposal No.1 landed: `_store_value_at_subscript(...)` now emits its key via
`_emit_subscript_key_object(...)`, completing store/load key-projection
symmetry for plain and unpack-target subscript stores. The slice is
bootstrap-verified `DONE_WEAK`. It does not prove the remaining enumerated
siblings (aug-assign subscript keys, dict-literal constructor keys,
weak-dict key policy), recursive valueclasses, complete V2 marshal coverage,
flattened storage, typed arrays, or total-goal completion.
