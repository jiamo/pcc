# Investigation: valueclass constructors in dynamic callable arguments should box as ValueBox

## Status
resolved locally 2026-06-04

## Problem Description
V2 valueclass constructor projection already covers `Any` returns, direct
`Any` formal calls, dynamic locals/literals, module globals, mutation stores,
and comprehensions. The adjacent pcc-native dynamic callable-object path still
materializes positional arguments through the generic call-args tuple helper.
That helper can turn a valueclass constructor payload into ordinary identity
instance semantics instead of a ValueBox before `py_obj_call`.

Predecessor investigations:

- `docs/investigations/valuebox-nested-valueclass-dynamic-equality-hash.md`
- `docs/investigations/valuebox-valueclass-local-container-projection.md`
- `docs/investigations/valuebox-valueclass-mutation-store-projection.md`
- `docs/investigations/valuebox-valueclass-comprehension-projection.md`

## Repro
Focused IR-shape repro:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dynamic_callable_arg_projection_boxes_valuebox -q -n0
```

Observed result before the fix: the generated `main` reaches `py_obj_call`, but
the IR has no `py_valuebox_new` in the dynamic call argument path.

Focused strict self-backend runtime repro:

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dynamic_callable_arg_projection_self_backend -q -n0
```

Observed result before the fix: strict `--backend self --python-libpython=off
--ir-scaffold=on` compilation succeeds, the binary prints `True`, `True`,
`False`, then raises `KeyError` at `table[same]`. The equality/hash behavior is
identity-like instead of ValueBox fieldwise equality/hash.

## Test [CONFIRMED]
Both focused tests above were run and observed failing on 2026-06-04 before the
source fix.

## Proposals
- No.1 Route `_emit_call_args_tuple` through object-boundary projection     [confirmed]
- No.2 Add pcc-native callable-instance `__call__` dispatch                    [confirmed]

## No.1 Route `_emit_call_args_tuple` through object-boundary projection
### Code Change
Use the existing valueclass constructor projection path for pcc-native dynamic
call arguments that are not CPython fallback values. This keeps the CPython
bridge branch intact while reusing the ValueBox projection logic already used
for locals/literals, mutation stores, and comprehensions.

### Result
Confirmed for the focused IR guard on 2026-06-04:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dynamic_callable_arg_projection_boxes_valuebox -q -n0
```

Result: 1 passed.

## No.2 Add pcc-native callable-instance `__call__` dispatch
### Code Change
The corrected repro uses `Keeper()(Segment(...))` so the dynamic boundary is a
pcc-native callable object rather than a CPython-backed function object assigned
to `Any`. `py_obj_call` now recognizes instance objects, looks up
`__call__`, and dispatches through the bound-method argument path. The
pcc-Python runtime mirror carries the same branch for staged bootstrap output.

### Result
Confirmed for the focused strict self-backend runtime repro on 2026-06-04 after
rebuilding both runtime archives:

```bash
env -u LC_ALL PATH=/Users/jiamo/my/pcc/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin make -B -C pcc/py_runtime libpy_runtime.a libpy_runtime_pcc_py.a
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dynamic_callable_arg_projection_self_backend -q -n0
```

Result: 1 passed.

Broad non-bootstrap gates confirmed on 2026-06-04:

- `tests/python/data_model/test_value_class_runtime.py` -> 22 passed
- `tests/python/test_py_value_class_unboxed.py` -> 20 passed
- V0/V1/status batch -> 37 passed
- `tests/python/gc_production_contract` -> 130 passed
- fallback/no-libpython baselines -> 18 passed
- touched-file `py_compile` -> passed

## Report

No.1 and No.2 landed. The first boundary was the generic dynamic
call-argument tuple path: `_emit_call_args_tuple(...)` emitted raw expressions
and generic `marshal_to_object(...)`, so a valueclass constructor crossing into
`py_obj_call` could become an ordinary identity instance instead of a ValueBox.
The fix routes call arguments through the same valueclass constructor
projection used by other object boundaries while preserving the CPython bridge
for fallback values.

The corrected pcc-native repro then exposed the callable-object runtime
boundary. `Keeper()(Segment(...))` uses an ordinary instance with `__call__`,
so `py_obj_call` now recognizes instance objects, looks up `__call__`, and
dispatches through the bound-method argument path. The pcc-Python runtime
mirror carries the same behavior for bootstrap-stage output.

Verification:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dynamic_callable_arg_projection_boxes_valuebox
  -> failed before the fix with no `py_valuebox_new`; passed after the fix
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dynamic_callable_arg_projection_self_backend
  -> failed before the fix with identity-like equality/hash and `KeyError`; passed after the fix
runtime archive rebuild
  -> passed (`make -B -C pcc/py_runtime libpy_runtime.a libpy_runtime_pcc_py.a`)
tests/python/data_model/test_value_class_runtime.py
  -> 22 passed
tests/python/test_py_value_class_unboxed.py
  -> 20 passed
V0/V1/status batch
  -> 37 passed
tests/python/gc_production_contract
  -> 130 passed
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  -> 18 passed
py_compile for touched Python files
  -> passed
git diff --check
  -> passed before bootstrap
local ignored-doc trailing-whitespace check
  -> passed
bootstrap: passed (tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -> 5 passed in 396.25s)
residual process check
  -> empty
```

Remaining value-model boundaries are unchanged: recursive valueclasses,
complete V2 marshal coverage, flattened object storage/dispatch, typed arrays,
monomorphization, typed-int projection repair, and full V-track completion
remain open.
