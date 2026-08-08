# Variadic call ABI focused evidence (2026-08-14)

Mode: host-pcc source inventory and LLVM emit-only. This evidence does not
claim the native Darwin/Linux `va_arg` execution or pcc1 fixed point.

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_runtime_port_variadic_c_abi_inventory_is_explicit \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_variadic_dynamic_call_intrinsics_emit_exact_c_abi
2 passed in 1.24s
```

The current runtime source inventory rejects fixed declarations for known
variadic targets, records `PyOS_snprintf` as variadic, and routes
`curl_easy_setopt` through the exact i32-return ABI. The generated calls use
`i32 (ptr, i32, ...)` and widen their results at the Python boundary. Native
Darwin/Linux register/stack behavior, archive gates, and sequential pcc1
remain open.
