# Investigation: valueclass constructors in builtin object-boundary calls should box as ValueBox

## Status
resolved

## Problem Description

V2 valueclass constructor projection covers many object-boundary positions, but
direct builtin calls that consume ordinary `PyObject*` arguments still route
some arguments through `_emit_as_object(...)` or raw `_emit_expr(...)` plus
`marshal_to_object(...)`. For a direct non-recursive nested valueclass
constructor such as `Segment(Point(...), Point(...))`, that can materialize an
ordinary identity instance before the ValueBox projection has a chance to run.

Related predecessors:

- `docs/investigations/valuebox-nested-valueclass-dynamic-equality-hash.md`
- `docs/investigations/valuebox-valueclass-local-container-projection.md`
- `docs/investigations/valuebox-valueclass-dynamic-callable-argument-projection.md`
- `docs/investigations/valuebox-valueclass-conditional-expression-projection.md`
- `docs/investigations/valuebox-valueclass-short-circuit-projection.md`
- `docs/investigations/valuebox-valueclass-membership-needle-projection.md`

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_builtin_object_boundary_boxes_valuebox \
  -q -n0
```

Observed before the fix: generated `main` still calls
`user_value_mod_Segment___init__` while preparing direct builtin arguments.

```bash
env -u LC_ALL uv run pytest \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_builtin_object_boundary_self_backend \
  -q -n0
```

Observed before the fix: strict self-backend compiles, then the binary exits
with `TypeError: unhashable type` at direct `hash(Segment(...))`.

## Test [CONFIRMED]

The focused pair was run together:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_builtin_object_boundary_boxes_valuebox \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_builtin_object_boundary_self_backend \
  -q -n0
```

Observed result: `2 failed`. The IR guard fails on
`user_value_mod_Segment___init__`; the strict runtime regression fails with
`TypeError: unhashable type` at direct `hash(Segment(...))`.

## Proposals

- No.1 Route selected builtin object-boundary arguments through direct
  constructor projection     [CONFIRMED]

## No.1 Route selected builtin object-boundary arguments through direct constructor projection

### Code Change

Use `_emit_expr_as_pcc_object(...)` for the selected builtin object-boundary
arguments where direct valueclass constructors should become ValueBoxes before
ordinary instance materialization:

- `repr(x)`, `ascii(x)`, and `hash(x)` in `call_expression_lowering.py`
- the value argument of `format(value, spec)` in `call_expression_lowering.py`
- `type(x)` and non-string `str(x)` in `builtin_type_attr_lowering.py`
- the `type(x).__name__` shortcut in `attr_load_lowering.py`

Keep identity-sensitive `id(x)` unchanged; it is not part of this projection
slice.

### CONFIRMED

The first half of the patch routed `repr`, `ascii`, `hash`,
`format(value, spec)`, `type(x)`, and non-string `str(x)` through
`_emit_expr_as_pcc_object(...)`. That made the strict runtime regression pass,
but the IR guard still found ordinary instance allocation in
`type(Segment(...)).__name__`. The remaining source was the dedicated
`attr_load_lowering.py` shortcut for `type(x).__name__`, which called
`_emit_as_object(...)` before `py_obj_type_name`.

After routing that shortcut through `_emit_expr_as_pcc_object(...)`, the
focused pair is green:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_builtin_object_boundary_boxes_valuebox \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_builtin_object_boundary_self_backend \
  -q -n0
```

Observed result: `2 passed`.

## Report

No.1 landed. The selected builtin object-boundary call sites now use the same
direct-constructor-aware projection helper as the other V2 escape-boundary
slices:

- `repr(x)`, `ascii(x)`, `hash(x)`, and `format(value, spec)` in
  `call_expression_lowering.py`
- `type(x)` and non-string `str(x)` in `builtin_type_attr_lowering.py`
- `type(x).__name__` in `attr_load_lowering.py`

Validation:

- focused IR guard -> 1 passed
- focused strict self-backend runtime regression -> 1 passed
- full valueclass runtime plus full unboxed suite -> 68 passed
- V0/V1/status batch -> 50 passed
- touched-path builtin/type/format/hash batch -> 18 passed
- full GC common contract -> 130 passed
- fallback/no-libpython baselines -> 18 passed
- touched-file `py_compile` passed
- `git diff --check` passed
- mandatory full self bootstrap -> 5 passed in 492.20s

This closes only the selected direct builtin object-boundary forms for the
non-recursive nested `Segment(start: Point, end: Point)` shape. It does not
prove complete builtin coverage, `getattr`/`hasattr` completeness,
identity-sensitive `id(...)` changes, recursive valueclasses, complete V2
marshal coverage, flattened storage, typed arrays, full V-track completion, or
total goal completion.
