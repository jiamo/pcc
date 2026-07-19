# Investigation: valueclass constructor augmented-subscript keys bypass object projection

## Status
resolved

## Problem Description

Third entry in the subscript store-key family (predecessors:
`valuebox-valueclass-subscript-store-key-projection.md` for plain stores,
`valuebox-valueclass-unpack-subscript-store-key-projection.md` for
unpack-target stores; both enumerated this site). Augmented subscript
assignment `d[k] += rhs` lowers in
`assignment_statement_lowering.py::_emit_augassign` (Subscript, non-slice
branch) by emitting the key once with plain `_emit_expr` +
`marshal_to_object` and reusing it for both `py_obj_getitem` and
`py_obj_setitem`. A direct valueclass constructor key —
`table[Segment(Point(1, 2), Point(3, 4))] += 5` — therefore materializes as
a legacy identity instance instead of a boxed valuebox, raising
`TypeError: unhashable type` at the load and surfacing as `KeyError`/abort,
while plain stores and loads of the same key are already projected.

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_aug_subscript_key_projection_boxes_valuebox' \
  'tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_aug_subscript_key_projection_self_backend' \
  -q -n0
```

Observed red (2026-06-10): IR guard fails with
`call ... @user_value_mod_Segment___init__` present in `@main`; the strict
self-backend probe exits 1.

## Test [CONFIRMED]

- `tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_aug_subscript_key_projection_boxes_valuebox`
  — red observed under the command above.
- `tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_aug_subscript_key_projection_self_backend`
  — red observed under the command above (probe exit 1).

## Proposals
- No.1 Emit the aug-assign subscript key via `_emit_subscript_key_object(...)`   [CONFIRMED]

## No.1 Emit the aug-assign subscript key via `_emit_subscript_key_object(...)`

### Code Change

In `assignment_statement_lowering.py::_emit_augassign`, Subscript non-slice
branch: replace `idx_val = self._emit_expr(stmt.target.idx)` +
`marshal.marshal_to_object(..., idx_val, stmt.target.idx.ty)` with
`idx_obj = self._emit_subscript_key_object(stmt.target.idx)`. The key is
still emitted exactly once and reused for both the getitem and the setitem,
preserving single-evaluation semantics.

Remaining enumerated siblings (not this slice): dict-literal constructor
keys (`{Segment(...): v}`), weak-dict store-key policy.

### CONFIRMED

Observed gate results after the one-site change (2026-06-10, this host):

- Focused IR guard + strict self-backend runtime regression (repro command
  above) -> 2 passed in 24.45s; probe prints `15 / True / 1`.
- Full `tests/python/test_py_value_class_unboxed.py -q -n0` -> 36 passed.
- Full `tests/python/data_model/test_value_class_runtime.py -q -n0`
  (`not nested_valuebox` / `nested_valuebox` halves) -> 14 + 24 = 38 passed.
- V0/V1/status batch -> 53 passed.
- Full GC common contract `tests/python/gc_production_contract -q -n0` ->
  130 passed.
- Fallback/no-libpython baselines -> 18 passed.
- Touched-file `py_compile` passed; `git diff --check` passed; residual
  process check empty.
- Mandatory full five-GC self bootstrap matrix
  `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py -q -n0` -> 5 passed
  in 977.09s (run backgrounded/CPU-throttled, hence slower than the
  foreground 375-469s runs; fresh stage2/stage3 per backend, normalized
  `pcc2 == pcc3`).

## Report

Proposal No.1 landed: the aug-assign subscript key is emitted once via
`_emit_subscript_key_object(...)` and reused for both getitem and setitem.
With the plain-store and unpack-store predecessors, all three subscript
store-key entries now share the load side's projection helper. The slice is
bootstrap-verified `DONE_WEAK`. Remaining enumerated siblings: dict-literal
constructor keys (`{Segment(...): v}`), weak-dict store-key policy (an
identity-semantics design question). This does not prove recursive
valueclasses, complete V2 marshal coverage, flattened storage, typed
arrays, or total-goal completion.
