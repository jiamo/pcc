# Investigation: NumPy 2.5 core exposes five missing generic C-API surfaces

## Status
resolved

## Problem Description
The generic pcc-native Meson target replay compiles 134 of the 139 actions in
NumPy 2.5.1's `_multiarray_umath` closure. Five source files fail because the
curated PCC C-API headers do not yet declare `PYMEM_DOMAIN_RAW`,
`Py_TPFLAGS_SEQUENCE`, `PyLong_IsZero`, `PyType_Modified`, or
`PyUnicode_FromObject`. This investigation is a successor to
`python-no-libpython-numpy-build-pcc-capi-include-redirect.md`; it covers the
generic API semantics only, not NumPy-specific branches.

## Repro

Configure the NumPy 2.5.1 sdist with a stable host Python, then call
`pcc.package.build_exec.execute_build_actions` with
`from_compile_commands=True`, the Meson target
`numpy/_core/_multiarray_umath.cpython-313-darwin.so`, `abi_mode="pcc-native"`,
and `jobs=2`. The deterministic result before the fix is 140 actions, five
compile failures plus one blocked link. The five first diagnostics are the
undeclared APIs named above.

## Test [CONFIRMED]

`tests/python/test_pcc_native_extension_loader.py::test_pcc_native_extension_numpy_25_capi_batch_under_self_backend_no_libpython`
is the minimized package-neutral extension gate. Before implementation its C
compile fails on the same five missing declarations.

## Proposals

- No.1 Implement the five public C-API semantics in the generic shim [CONFIRMED]

## No.1 Implement the five public C-API semantics in the generic shim

### Code Change

Mirror CPython's public constant values; make tracemalloc domain 0 explicit;
implement exact integer zero testing without narrowing bignums; make
`PyType_Modified` a no-op because PCC has no CPython type lookup cache to
invalidate; and return a new reference from `PyUnicode_FromObject` for PCC
Unicode objects while raising `TypeError` for other objects.

### CONFIRMED

The focused generic extension test passes, and exact Meson target replay for
NumPy 2.5.1 completes all 140 `_multiarray_umath` actions and all 8
`_umath_linalg` actions. The resulting dylibs have neither a libpython edge nor
a CPython ABI tag.

## Report

Proposal No.1 landed as the generic C-API fix. It closes the five compile
failures without package-name dispatch. A later end-to-end import exposed a
separate language-version selection boundary, tracked in
`package-acquisition-target-python.md`; it is not a C-API regression.
