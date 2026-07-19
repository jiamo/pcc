# Investigation: valueclass constructors in set builtin literal sources should box as ValueBox

## Status
resolved

## Problem Description
The V2 valueclass projection matrix has covered Any locals, containers,
mutation stores, comprehensions, call arguments, attributes, conditionals,
short-circuit expressions, `dict(...)` keyword values, and
`dict.update(...)` keyword values. The remaining `set([valueclass(...)])` and
`set((valueclass(...),))` literal-source paths were not listed as covered.

Code inspection showed `_maybe_emit_set_builtin` emitted each list/tuple source
element with raw `_emit_expr(...)` followed by `marshal_to_object(...)`. That
bypasses the generic `_emit_expr_as_pcc_object(...)` projection helper used by
other dynamic/container boundaries and can leave nested valueclass constructors
materialized through ordinary instance allocation.

Predecessors:
- `valuebox-valueclass-mutation-store-projection.md`
- `valuebox-valueclass-comprehension-projection.md`
- `valuebox-valueclass-dict-builtin-keyword-projection.md`
- `valuebox-valueclass-dict-update-keyword-projection.md`

## Repro
```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_set_builtin_literal_source_projection_boxes_valuebox -q -n0
```

Expected failing marker before the fix: the generated `main` IR still contains
`call ptr (ptr) @py_instance_new` for the set literal-source valueclass
elements.

## Test [CONFIRMED]
The focused IR regression above was run on 2026-06-04 and failed with:

```
AssertionError: assert not <re.Match ... match='call ptr (ptr) @py_instance_new'>
```

An initial broader test shape that iterated the set and compared `item == same`
hit an existing ClassType direct-comparison lowering gap first, so the focused
test was narrowed to set construction, duplicate collapse, and membership.

## Proposals
- No.1 Use the generic PyObject projection helper for set builtin literal-source elements     [CONFIRMED]

## No.1 Use the generic PyObject projection helper for set builtin literal-source elements
### Code Change
In `pcc/py_frontend/codegen/set_lowering.py`, change the literal list/tuple
source branch of `_maybe_emit_set_builtin` from raw `_emit_expr(...)` plus
`marshal_to_object(...)` to `_emit_expr_as_pcc_object(...)` for each non-spread
element.

### CONFIRMED
The literal-source branch now calls `_emit_expr_as_pcc_object(...)` for each
non-spread list/tuple source element before `py_set_add(...)`. This reuses the
same valueclass constructor -> ValueBox projection used by the other
object/container escape boundaries.

Focused gates after the code change:

```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_set_builtin_literal_source_projection_boxes_valuebox -q -n0
```

Result: 1 passed.

```
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_set_builtin_literal_source_self_backend -q -n0
```

Result: 1 passed.

Broader gates:

```
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py -q -n0
```

Result: 28 passed.

```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py -q -n0
```

Result: 26 passed.

```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_source_shape.py tests/python/data_model/test_value_class_field_flattening.py tests/python/test_value_model_valhalla.py -q -n0
```

Result: 43 passed.

```
env -u LC_ALL uv run pytest tests/python/gc_production_contract -q -n0
```

Result: 130 passed.

```
env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py -q -n0
```

Result: 18 passed.

Touched-file `py_compile` passed. `git diff --check` passed before bootstrap.

Mandatory V-track bootstrap gate:

```
env -u LC_ALL uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0
```

Result: 5 passed in 402.59s.

## Report
No.1 landed. It is the smallest generic fix because it only changes the
`set([..])` / `set((..))` literal-source element projection path and leaves
spread and generic iterable set construction unchanged.

This resolves the focused non-recursive nested
`Segment(start: Point, end: Point)` set builtin list/tuple literal-source
projection boundary. It does not prove source-level set iteration comparison
support, recursive valueclasses, complete V2 escape-boundary marshal coverage,
flattened valueclass storage, typed arrays, or full value-model completion.
