# Investigation: valueclass constructor projection through dataclasses.replace keyword overrides

## Status
resolved

## Problem Description
pcc's V2 valueclass/object-boundary coverage requires valueclass constructor
expressions to become `ValueBox` objects when they cross a dynamic object
boundary. The native `dataclasses.replace(...)` helper still emits direct keyword
override values through raw `_emit_expr(...)` followed by generic
`marshal_to_object(...)`, so a valueclass constructor override can be materialized
as an ordinary pcc instance instead of a boxed value payload.

This is a focused continuation of the V2 escape-boundary projection work. It
does not claim complete dataclasses support, `**kwargs` dictionary replacement,
recursive valueclasses, flattened storage, typed arrays, or full V-track
completion.

## Repro
Focused IR-shape repro:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dataclasses_replace_keyword_boxes_valuebox -q -n0
```

Observed current failure:

```text
AssertionError: assert not <re.Match ... @user_value_mod_Segment___init__ ...>
```

Focused strict self-backend runtime repro:

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dataclasses_replace_keyword_self_backend -q -n0
```

Observed current failure:

```text
TypeError: unhashable type
...
KeyError
```

## Test [CONFIRMED]
- `tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dataclasses_replace_keyword_boxes_valuebox`
  failed because generated `main` still calls
  `user_value_mod_Segment___init__` while preparing
  `replace(base, item=Segment(...))`.
- `tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dataclasses_replace_keyword_self_backend`
  failed under strict `--backend self --python-libpython=off --ir-scaffold=on`
  because the replaced dynamic field has ordinary identity-instance behavior
  rather than boxed `ValueBox` equality/hash behavior.

## Proposals
- No.1 Route direct replace keyword override values through
  `_emit_expr_as_pcc_object(...)` [CONFIRMED]

## No.1 Route direct replace keyword override values through `_emit_expr_as_pcc_object(...)`
### Code Change
In `pcc/py_frontend/codegen/native_dataclasses.py`, replace the direct keyword
override path's raw `_emit_expr(kw_expr)` plus `marshal_to_object(...)` sequence
with `_emit_expr_as_pcc_object(kw_expr)`. Leave the receiver object and
`**kwargs` dictionary path unchanged in this focused slice.

### CONFIRMED
The direct keyword override path now calls `_emit_expr_as_pcc_object(kw_expr)`.
The receiver object and `**kwargs` dictionary path are intentionally unchanged in
this focused slice.

Focused verification:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dataclasses_replace_keyword_boxes_valuebox -q -n0
```

Result: 1 passed.

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dataclasses_replace_keyword_self_backend -q -n0
```

Result: 1 passed.

The IR guard was tightened so the base dataclass starts with `None`, isolating
the `replace(base, item=Segment(...))` override itself. This proves the focused
override now goes through `py_valuebox_new` / `py_valuebox_set_field` and does
not call the valueclass `__init__` path in `main`.

## Report
No.1 landed. Direct keyword override values in native
`dataclasses.replace(...)` now use the same object-boundary projection helper as
the other completed V2 valueclass constructor escape-boundary slices.

Final verification for the landed slice:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dataclasses_replace_keyword_boxes_valuebox -q -n0
```

Result: 1 passed.

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dataclasses_replace_keyword_self_backend -q -n0
```

Result: 1 passed.

Broader gates:

- full `tests/python/data_model/test_value_class_runtime.py` -> 33 passed
- full `tests/python/test_py_value_class_unboxed.py` -> 31 passed
- dataclasses related suite -> 11 passed
- V0/V1/status batch -> 48 passed
- full `tests/python/gc_production_contract` -> 130 passed
- fallback/no-libpython baselines -> 18 passed
- touched-file `py_compile` and `pcc/value_model.py` `py_compile` passed
- `tests/python/test_value_model_valhalla.py` -> 4 passed
- mandatory full self bootstrap
  `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0`
  -> 5 passed in 415.75s

This closes only the direct keyword override form. It does not prove complete
dataclasses support, `**kwargs` replacement dictionaries, recursive valueclasses,
complete V2 marshal coverage, flattened storage, typed arrays, full V-track, or
the total goal.
