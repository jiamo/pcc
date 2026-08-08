# Contextual class-method ABI focused evidence (2026-08-14)

Mode: host pcc, strict no-libpython, LLVM emit-only. This evidence does not
claim an executable runtime result or a pcc1/bootstrap fixed point.

The focused class-method lowering contracts are green:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_init_field_rhs_type.py::test_class_self_call_argument_types_reach_literal_dispatch_target \
  tests/python/test_native_init_field_rhs_type.py::test_full_pcc_gui_context_method_ints_follow_emitted_abi \
  tests/python/test_native_init_field_rhs_type.py::test_method_argument_provenance_pins_managed_but_not_raw_pointer
3 passed in 0.79s

gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_py_class_schema_type_infer.py::test_contextual_l1_codegen_host_param_types_host_methods
1 passed in 0.08s
```

These gates prove that the full saved GUI context and the minimized class
self-call use the emitted scalar ABI, and that managed arguments are pinned
while raw machine pointers are not. The final frozen-source LLVM/self
executables, multi-file/bootstrap-shim suites, and sequential pcc1 bootstrap
remain open.
