# Investigation: valueclass constructors in set method element arguments should box as ValueBox

## Status
resolved

## Problem Description
The V2 valueclass projection matrix now covers set builtin list/tuple
literal-source elements, but direct set method element arguments were not
covered. Code inspection showed `_maybe_emit_set_method(...)` routes
`add`, `remove`, and `discard` arguments through `_emit_as_object(...)`.
That helper emits a valueclass constructor expression first and only attempts
payload-to-object boxing afterward, so the constructor can already have
materialized as an ordinary instance.

Adjacent list/dict method object-argument paths use
`_emit_expr_as_pcc_object(...)`, which can project valueclass constructors
before ordinary instance allocation.

Predecessors:
- `valuebox-valueclass-mutation-store-projection.md`
- `valuebox-valueclass-dict-update-keyword-projection.md`
- `valuebox-valueclass-set-builtin-literal-source-projection.md`

## Repro
```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_set_method_element_projection_boxes_valuebox -q -n0
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_set_method_element_self_backend -q -n0
```

Expected failing markers before the fix:
- the generated `main` IR still contains `call ptr (ptr) @py_instance_new`
  for direct `set.add/remove/discard(Segment(...))` arguments;
- the strict self-backend runtime probe prints identity-instance behavior
  (`len(items) == 3`, membership for the equal ValueBox is `False`) instead
  of duplicate collapse and boxed equality/hash membership.

## Test [CONFIRMED]
Both focused regressions were run on 2026-06-04 and failed as expected:

```
AssertionError: assert not <re.Match ... match='call ptr (ptr) @py_instance_new'>
```

and:

```
AssertionError: assert ['3', 'False', ...] == ['2', 'True', ...]
```

## Proposals
- No.1 Use the generic PyObject projection helper for set method element arguments     [CONFIRMED]

## No.1 Use the generic PyObject projection helper for set method element arguments
### Code Change
In `pcc/py_frontend/codegen/set_lowering.py`, route the shared item argument
used by `add`, `remove`, and `discard` through `_emit_expr_as_pcc_object(...)`
instead of `_emit_as_object(...)`.

### CONFIRMED
The shared item path used by `set.add`, `set.remove`, and `set.discard` now
calls `_emit_expr_as_pcc_object(...)`. This lets valueclass constructor
arguments project to ValueBox objects before ordinary instance allocation.

Focused gates after the code change:

```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_set_method_element_projection_boxes_valuebox -q -n0
```

Result: 1 passed.

```
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_set_method_element_self_backend -q -n0
```

Result: 1 passed.

Broader gates:

```
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py -q -n0
```

Result: 29 passed.

```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py -q -n0
```

Result: 27 passed.

```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_source_shape.py tests/python/data_model/test_value_class_field_flattening.py tests/python/test_value_model_valhalla.py -q -n0
```

Result: 44 passed.

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

Result: 5 passed in 401.07s.

## Report
No.1 landed. It is intentionally scoped to the shared set element-argument path
for `add`, `remove`, and `discard`; set algebra methods that take another
collection were left unchanged.

This resolves the focused non-recursive nested
`Segment(start: Point, end: Point)` set method element projection boundary. It
does not prove recursive valueclasses, complete V2 escape-boundary marshal
coverage, flattened valueclass storage, typed arrays, or full value-model
completion.
