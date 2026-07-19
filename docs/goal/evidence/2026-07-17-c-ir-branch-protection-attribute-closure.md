# AUD-P1-C-IR-POSTPROCESS-POLICY closure evidence

`postprocess_ir_text()` and its report variant now dispatch only the narrow
va_arg rewrite. AArch64 branch protection is attached to every defined C
function during `LLVMCodeGenerator.generate_code()` as native LLVM target
string attributes:

```text
"branch-target-enforcement"
"sign-return-address"="non-leaf"
"sign-return-address-key"="a_key"
```

The cross-builder compatibility helper adds attributes directly to the pcc
LLVM-C IR builder and to llvmlite's IR attribute collection; it never searches
or rewrites serialized IR. A real llvmlite AArch64 target-machine gate emits
`paciasp` plus `retaa`/`autiasp`. The first-class self backend remains
independent and its existing prologue/epilogue gates still emit `paciasp` and
`autiasp`.

Focused gates:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/c/test_c_branch_protection_policy.py tests/c/test_c_varargs_split.py tests/c/test_varargs.py tests/c/test_llvm_capi_ir_parity.py tests/python/test_cli_self_backend_vectorize_policy.py
35 passed in 1.56s

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 tests/c/test_self_backend.py::test_self_backend_aarch64_prologue_helper_covers_arg_spills_and_hidden_sret tests/c/test_self_backend.py::test_self_backend_aarch64_terminator_helpers_cover_epilogue_branch_and_switch
2 passed in 0.25s
```

No broad LLVM-CAPI refactor, full bootstrap, or full GCC suite was run.
