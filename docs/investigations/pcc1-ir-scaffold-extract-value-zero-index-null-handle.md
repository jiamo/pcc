# Investigation: pcc1 passes aggregate index zero as a NULL scaffold handle

## Status

active

## Problem Description

A current-source project-local `pcc1` cannot compile a typed C ABI function that extracts lane zero from a `{f64,f64}` argument. The scaffold lowering declares every required `IRBuilder.extract_value` operand as an opaque pointer and converts the native integer index with `inttoptr`. Index zero therefore crosses the call as `NULL`, while the compiled `IRBuilder.extract_value` implementation expects a Python integer or iterable and calls `py_obj_iter(NULL)`.

This blocks the freestanding runtime archive at `PyComplex_FromCComplex` and therefore blocks rebuilding the PCC-native DeepSeek Harness after the Cordis effect implementation changed. The typed aggregate declaration itself is not the failing operation: LLDB locates the first invalid value in `IRBuilder.extract_value`.

## Repro

```bash
gtimeout 240s env -u LC_ALL \
  PCC_CURRENT_PCC1=projects/harness/build/pcc1 \
  uv run pytest -q -x -n0 \
  tests/python/test_typed_c_abi_aggregate.py::test_current_pcc1_compiles_structural_f64_pair_export
```

Expected: exit code `0` and emitted IR containing `define double @pcc_pair_sum({ double, double }`.

Observed 2026-08-14: exit code `1` with `PCC-PY-COMPILE-001: [python-frontend] py_obj_iter received NULL object`.

The native caller was localized with:

```bash
gtimeout 90s env -u LC_ALL -u LC_CTYPE lldb -b \
  -o 'breakpoint set -n py_obj_iter -c "$x0 == 0"' \
  -o 'run --python-library --python-libpython=off --emit-llvm=/tmp/typed-pair.ll <typed-pair-source>' \
  -o 'bt 30' \
  -- projects/harness/build/pcc1
```

The first project frame is `user_pcc_llvm_capi_ir_IRBuilder_extract_value`, called by `UnsafeIntrinsicMixin__emit_unsafe_intrinsic_call` while lowering `f64_pair_first(value)`.

## Test [CONFIRMED]

`tests/python/test_typed_c_abi_aggregate.py::test_current_pcc1_compiles_structural_f64_pair_export` is the minimized current-pcc1 regression. It failed deterministically before a code change.

The existing CPython-hosted test `test_typed_c_abi_export_supports_structural_f64_pair_argument_and_return` is the source-semantics oracle and already passes, separating this defect from typed C ABI parsing and aggregate ABI lowering.

## Proposals

- No.1 Box scaffold operands that the callee consumes as Python integers [pending]
- No.2 Special-case zero inside `IRBuilder.extract_value` [DENIED]

## No.1 Box scaffold operands that the callee consumes as Python integers

### Code Change

Add an explicit scaffold parameter classification for Python integer operands and use `py_int_from_i64` when lowering the `indices` operand of `extract_value` and `insert_value`. Preserve the existing raw-i64 classification for methods whose compiled ABI declares a native integer and preserve bit-handle lowering for operands that intentionally carry machine values.

### pending

The minimized current-pcc1 test, the host scaffold method tests, a rebuilt current-source `pcc1`, direct compilation of `py_capi_type_runtime.py`, and the native Harness build remain required.

## No.2 Special-case zero inside `IRBuilder.extract_value`

### Code Change

Interpret a `NULL` `indices` parameter as aggregate lane zero.

### DENIED

`NULL` is an invalid Python argument and cannot distinguish an accidentally erased object from the integer zero. Treating it as lane zero would hide the cross-module ABI mismatch and leave every nonzero integer operand encoded as an invalid raw pointer.
