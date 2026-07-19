# 2026-07-16 valueclass zero-allocation hot-loop evidence

Task: `M3-VALUECLASS-ZERO-ALLOC`

## Combined proof

`test_valueclass_hot_loop_zero_allocation_oracle_and_escape_semantics` is the
single commit-bound regression joining all four required observations:

1. LLVM IR for a bounded 1000-iteration loop keeps `Point` as a
   `{i64, i64}` aggregate.  The hot body has no `py_instance_new` or
   `py_valuebox_new`; the explicit `escape(...) -> Any` function does call
   `py_valuebox_new` and does not call the ordinary instance constructor.
2. Strict self/no-libpython executables for otherwise identical `range(0)` and
   `range(1000)` sources emit identical runtime `alloc_object` counts and
   identical allocation type-tag histograms.  Each has exactly two
   instance-tag allocations, corresponding to the two explicit escapes; the
   additional 1000 valueclass iterations add no heap allocation.
3. The hot result matches an ordinary Python class oracle.
4. Escaped boxes preserve alias identity, distinct-box identity, `id` aliasing,
   and box dynamic attributes.  Weak references retain the established
   valueclass `TypeError` policy, while raw-payload identity/dynamic-attribute
   surfaces remain compile-time diagnostics.

The proof deliberately permits tagged-int projection and bignum slow-path
calls in IR.  Treating every call as allocation would be false and would
encourage weakening Python arbitrary-precision semantics.

## Gates

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_value_class_unboxed.py

47 passed in 5.95s
```

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/data_model/test_value_class_source_shape.py \
  tests/python/data_model/test_value_class_runtime.py \
  tests/python/test_valueclass_weakref_runtime.py \
  -k 'rejects_identity_escape_operations or rejects_instance_identity_surfaces or identity_diagnostics_do_not_capture_shadowed_builtin_names or boxed_identity_observes_box_identity or dyn_valuebox_weakref_raises_typeerror'

12 passed, 56 deselected in 8.07s
```

Formatting and `py_compile` also passed for the touched test file.

## Claim boundary

This proves one bounded valueclass hot loop is allocation-free at runtime in
the Darwin-arm64 self/no-libpython mode, with a matching LLVM shape and
explicit escape semantics.  It does not claim arbitrary loops, unbounded
Python-int accumulators, all valueclass layouts, or compilation/startup itself
are allocation-free.

