# Investigation: valueclass constructors in sequence builtin literal sources should box as ValueBox

## Status
resolved

## Problem Description
The V2 valueclass projection matrix covers list/tuple/dict literals and set
builtin list/tuple literal-source construction, but `list(...)` and
`tuple(...)` builtin construction from literal list/tuple sources were not
covered. Code inspection showed both `_maybe_emit_list_builtin(...)` and
`_emit_tuple_builtin(...)` emitted each literal-source element through raw
`_emit_expr(...)` followed by `marshal_to_object(...)`.

That sequence can materialize a valueclass constructor as an ordinary instance
before ValueBox projection, producing identity/unhashable behavior at later
dynamic object boundaries.

Predecessors:
- `valuebox-valueclass-local-container-projection.md`
- `valuebox-valueclass-set-builtin-literal-source-projection.md`
- `valuebox-valueclass-set-method-element-projection.md`

## Repro
```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_sequence_builtin_literal_source_boxes_valuebox -q -n0
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_sequence_builtin_literal_source_self_backend -q -n0
```

Expected failing markers before the fix:
- the generated `main` IR still contains `call ptr (ptr) @py_instance_new`
  for `list([Segment(...)])` / `tuple((Segment(...), ...))` source elements;
- the strict self-backend runtime probe exits with `TypeError: unhashable type`
  and `KeyError` at a dict lookup keyed by a builtin-produced sequence element.

## Test [CONFIRMED]
Both focused regressions were run on 2026-06-04 and failed as expected:

```
AssertionError: assert not <re.Match ... match='call ptr (ptr) @py_instance_new'>
```

and:

```
TypeError: unhashable type
...
KeyError
```

## Proposals
- No.1 Use the generic PyObject projection helper for sequence builtin literal-source elements     [confirmed]

## No.1 Use the generic PyObject projection helper for sequence builtin literal-source elements
### Code Change
In `pcc/py_frontend/codegen/list_builtin_lowering.py` and
`pcc/py_frontend/codegen/tuple_zip_lowering.py`, change literal list/tuple
source element emission from raw `_emit_expr(...)` plus `marshal_to_object(...)`
to `_emit_expr_as_pcc_object(...)`.

### Result
Confirmed. Routing literal-source elements through `_emit_expr_as_pcc_object(...)`
boxes valueclass constructor payloads as `ValueBox` at the builtin sequence
object boundary instead of first materializing ordinary instances.

Focused gates:

```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_sequence_builtin_literal_source_boxes_valuebox -q -n0
# 1 passed in 0.23s

env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_sequence_builtin_literal_source_self_backend -q -n0
# 1 passed in 27.04s
```

Broad gates:

```
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py -q -n0
# 30 passed in 19.35s

env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py -q -n0
# 28 passed in 5.31s

env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_source_shape.py tests/python/data_model/test_value_class_field_flattening.py tests/python/test_value_model_valhalla.py -q -n0
# 45 passed in 5.99s

env -u LC_ALL uv run pytest tests/python/gc_production_contract -q -n0
# 130 passed in 24.09s

env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py -q -n0
# 18 passed in 118.52s

env -u LC_ALL uv run python -m py_compile <touched Python files>
# passed

git diff --check
# passed before docs sync
```

Mandatory bootstrap:

```
env -u LC_ALL uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0
# 5 passed in 413.45s
```

Non-claim: this proves the focused `list(...)` / `tuple(...)` literal-source
element projection and boxed equality/hash behavior for the non-recursive
nested `Segment(start: Point, end: Point)` shape. It does not prove recursive
valueclasses, complete V2 marshal coverage, flattened storage, typed arrays,
full V-track, or total-goal completion.
