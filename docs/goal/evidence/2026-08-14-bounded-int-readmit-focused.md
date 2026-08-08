# Bounded integer readmission focused evidence (2026-08-14)

Mode: host-pcc LLVM emit-only. No runtime benchmark or pcc1/bootstrap was run.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_py_typed_int_unboxed.py::test_proven_bounded_typed_int_loop_defaults_to_unboxed_shape \
  tests/python/test_py_typed_int_unboxed.py::test_proven_literal_list_accumulator_defaults_to_unboxed_shape \
  tests/python/test_py_typed_int_unboxed.py::test_proven_direct_call_accumulator_defaults_to_unboxed_calls \
  tests/python/test_py_typed_int_unboxed.py::test_unproven_scalar_loop_keeps_arbitrary_precision_boxed_abi \
  tests/python/test_py_typed_int_unboxed.py::test_out_of_i64_literal_list_keeps_arbitrary_precision_boxed_loop
6 passed in 1.31s
```

Exactly the scalar, literal-list and pure direct-call bounded accumulator
shapes use the i64 lane. Escaped/unbounded calls and out-of-i64 list values
remain boxed with arbitrary-precision operations. The security/overflow
battery, pinned >=10% speedup and final bootstrap remain open.
