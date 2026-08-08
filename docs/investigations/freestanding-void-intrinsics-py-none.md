# Investigation: freestanding void intrinsics materialize `py_None`

## Status

resolved

## Problem Description

The strict freestanding pcc-Python module contract rejects the new memory and
string substrate because a side-effect-only `pcc.unsafe` intrinsic such as
`store_i8()` emits a load from `@py_None` after its raw store.  That managed
runtime reference prevents an otherwise closed raw object from being linked as
the shared Python/C-frontend libc implementation.

## Repro

Run:

```bash
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_module.py::test_freestanding_void_unsafe_intrinsic_does_not_materialize_py_none
```

Expected: the exported body contains the byte store and no managed-runtime
reference.  Before the fix, compilation raises `PyPipelineError` naming the
`load ptr, ptr @py_None` instruction.

## Test [CONFIRMED]

`tests/python/test_freestanding_module.py::test_freestanding_void_unsafe_intrinsic_does_not_materialize_py_none`
is the minimized public-pipeline regression.  The full memory/string module is
the downstream closure check.

## Proposals

- No.1 Return a raw null sentinel for void unsafe intrinsics only in strict
  freestanding mode [CONFIRMED]

## No.1 Return a raw null sentinel for void unsafe intrinsics only in strict freestanding mode

### Code Change

Add one `UnsafeIntrinsicMixin` result helper that preserves ordinary Python
mode's `py_None` behavior but returns an unowned raw null pointer under the
strict freestanding contract.  Route only side-effect-only unsafe intrinsics
through it; value-producing intrinsics and ordinary Python calls are unchanged.

### CONFIRMED

The minimized regression changed from the validator error naming
`load ptr, ptr @py_None` to a clean raw `store i8` body.  The helper preserves
the existing `py_None` path outside strict freestanding mode.  The downstream
memory/string module then compiled through both LLVM and the self backend into
objects with no undefined symbols, and a PCC C-frontend self-backend consumer
linked and ran against that pcc-Python object.

Focused gate:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_module.py \
  tests/python/test_freestanding_mem_str.py \
  tests/python/test_unsafe_atomics.py \
  tests/python/test_atomic_mirror_gap.py
45 passed in 54.11s
```

## Report

No.1 landed as the smallest mode-scoped correction.  It avoids introducing a
second statement-lowering path and keeps normal Python `None` semantics intact.
The full freestanding mem/str tests provide the downstream proof that the raw
sentinel does not become a managed ownership operation or an unresolved link
dependency.
