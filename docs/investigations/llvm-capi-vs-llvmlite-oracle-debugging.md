# `llvm_capi` vs `llvmlite` Oracle Debugging

This note records the debugging pattern that worked best for recent
`pcc.llvm_capi` regressions.

The short version:

1. shrink the failure to the smallest C reproducer you can
2. run the same reproducer through both backends
3. use the `llvmlite` path as the oracle for codegen / IR-builder behavior
4. patch the smallest missing semantic in `pcc.llvm_capi`
5. add one focused regression and re-run the realistic confirmation

This is the fastest way to debug bugs in:

- IRBuilder API parity
- constant-expression lowering
- typed pointer semantics hidden by opaque `ptr`
- constant GEP / bitcast expression lowering
- function-pointer / function-type decay issues

It is **not** the right first tool for:

- system preprocessor behavior
- header rewrite shims
- compile-only diagnostics policy
- parser acceptance / rejection mismatches

Those live above the IR-builder layer and often fail before backend parity
matters.

## Why this works

`pcc/codegen/c_codegen.py` imports the backend through:

- [/Users/jiamo/my/pcc/pcc/llvm_capi/compat.py](/Users/jiamo/my/pcc/pcc/llvm_capi/compat.py)

The C frontend defaults to `pcc.llvm_capi`, but can be switched back to
`llvmlite` with:

```bash
PCC_USE_LLVMLITE_C=1
```

That gives a built-in oracle path for the same source, the same codegen, and
the same test harness.

## Standard workflow

### 1. Reproduce with a tiny focused case

Do not start from full `pytest -q`. Start from one test or one minimized C
snippet:

```bash
env -u LC_ALL uv run pytest 'tests/test_clang_compat.py::test_unsigned_int_to_float_conversion_uses_unsigned_semantics' -q -n0
```

or:

```bash
env -u LC_ALL uv run python /tmp/repro.py
```

### 2. Re-run under `llvmlite`

Use the exact same repro with the oracle backend:

```bash
PCC_USE_LLVMLITE_C=1 env -u LC_ALL uv run pytest 'tests/test_clang_compat.py::test_unsigned_int_to_float_conversion_uses_unsigned_semantics' -q -n0
```

Interpretation:

- `llvmlite` passes, `llvm_capi` fails:
  likely a backend parity gap
- both fail the same way:
  likely not a backend swap bug
- both compile but runtime differs:
  inspect generated IR next

### 3. Compare IR, not guesses

For codegen / constant-expression bugs, dump the pre-optimization IR from the
same source under both modes.

Typical pattern:

```python
from pcc.evaluater.c_evaluator import (
    CEvaluator,
    _compile_translation_unit_artifact_job,
    TranslationUnit,
)
import os

source = r'''
int main(void) {
    unsigned int high_bit = ~((~0U) >> 1);
    float value = (float)high_bit;
    return value > 0.0f && value == 2147483648.0f ? 0 : 1;
}
'''

ce = CEvaluator()
base_dir = os.getcwd()
unit = TranslationUnit(
    name="__pcc_eval__.c",
    path=os.path.join(base_dir, "__pcc_eval__.c"),
    source=source,
)
artifact = _compile_translation_unit_artifact_job(
    unit,
    base_dir,
    ce._has_system_cpp(),
    None,
    None,
    None,
    False,
    ce._normalize_opt_level(False),
    ce.backend_sig,
)
print(artifact["ir_text"])
```

Run it twice:

- default environment for `llvm_capi`
- `PCC_USE_LLVMLITE_C=1` for `llvmlite`

Then compare the smallest structural difference.

### 4. Fix the smallest semantic gap only

Examples of real minimal parity fixes that fell out of this workflow:

- add missing builder ops:
  - `uitofp`
  - `fptoui`
  - `fpext`
  - `fneg`
- make constant-expression `Value.gep()` / `Value.bitcast()` work
- treat `int ()` parameters as function-pointer decay targets instead of fixed
  zero-arg function types
- avoid equating all opaque `ptr` types just because their text form is `ptr`

Do **not** broaden the patch until the smallest repro is green.

### 5. Lock the fix in two ways

After the patch:

1. add a focused regression test
2. rerun one realistic case that originally failed

Examples:

```bash
env -u LC_ALL uv run pytest 'tests/test_clang_compat.py::test_function_typed_parameter_decl_decays_to_function_pointer' -q -n0
env -u LC_ALL uv run pytest 'tests/test_gcc_torture_execute.py::test_gcc_torture_runtime_succeeds_under_native_and_pcc[conversion.c]' -q -n0
```

## What `llvmlite` is good at as an oracle

Use it first for:

- missing builder methods
- malformed instruction signatures
- bad function pointer call IR
- constant global initializer lowering
- pointer arithmetic / pointer-difference semantics
- cast semantics hidden by opaque pointers

These are all places where the llvmlite path already encodes the intended
behavior.

## What `llvmlite` is *not* the oracle for

Do not force every failure into a backend-parity story.

Examples where `llvmlite` is not the main answer:

- `system cpp` include failures
- fake-libc or header shim policy
- compile-only diagnostic expectations
- manifest drift
- parser acceptance / rejection differences

For those, compare:

- native compiler behavior
- `pcc/evaluater/c_evaluator.py` preprocessing policy
- parser / semantic validation behavior

## Practical commands

Focused backend comparison:

```bash
env -u LC_ALL uv run pytest 'tests/test_clang_compat.py::test_global_pointer_initializer_accepts_struct_member_address_expression' -q -n0
PCC_USE_LLVMLITE_C=1 env -u LC_ALL uv run pytest 'tests/test_clang_compat.py::test_global_pointer_initializer_accepts_struct_member_address_expression' -q -n0
```

Representative runtime confirmation:

```bash
env -u LC_ALL uv run pytest 'tests/test_gcc_torture_execute.py::test_gcc_torture_runtime_succeeds_under_native_and_pcc[930513-1.c]' -q -n0
env -u LC_ALL uv run pytest 'tests/test_c_files.py::test_c_file[struct_layout.c]' -q -n0
```

## Rule of thumb

If a failure smells like:

- "builder is missing an instruction"
- "IR parses under llvmlite but not under llvm_capi"
- "pointer / function-pointer types look wrong"
- "constant initializer became `null` or `0`"

then the fastest path is:

**minimize -> rerun with `PCC_USE_LLVMLITE_C=1` -> diff IR -> patch the
smallest semantic gap**

That should be the default playbook for future `llvm_capi` parity regressions.
