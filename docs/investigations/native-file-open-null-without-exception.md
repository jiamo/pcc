# Investigation: native file open returns NULL without an exception

## Status

resolved

## Problem Description

The complete non-integration suite failed
`test_pcc1_package_install_writes_manifest_without_host_python`: a compiled
pcc1 package install against a fresh Meson source tree exited successfully but
printed `None` instead of its JSON manifest. The failing path tried to read an
absent eager-build report inside `try: with open(...): ... except Exception:`.
The handler should have returned its fallback payload.

## Repro

The minimized semantic regression is:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_python_exception_parity.py::test_return_from_failed_with_except_survives_outer_finally
```

Before the runtime fix it failed deterministically with
`['cleanup', '<null>']` instead of `['cleanup', 'fallback']`.

The original package boundary is:

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_package_install.py::test_pcc1_package_install_writes_manifest_without_host_python
```

Before minimization, the subprocess exited zero and wrote `None` rather than a
JSON object.

## Test [CONFIRMED]

The minimized test above was observed failing on 2026-07-22. LLVM and self
backend binaries both reproduced the wrong result, ruling out self-backend
assembly emission. LLDB stopped in the success-path return-root store with a
NULL value after `py_file_open` had failed; the exception handler was never
entered.

## Proposals

- No.1 Initialize the with-open GC root slot at function entry [DENIED]
- No.2 Make native open obey the NULL-plus-exception contract [CONFIRMED]

## No.1 Initialize the with-open GC root slot at function entry

### Code Change

Create a newly synthesized `with open(...) as fh` pointer alloca with
`init_null=True` before its entry-block GC frame registration.

### DENIED

This is required root hygiene once the open call can branch around the binding
store, but it is not the cause of the observed NULL return. After this change
alone, the minimized test still failed with `['cleanup', '<null>']` in 7.37s.
The initializer remains as a companion invariant: every registered root slot
must contain an object pointer or NULL on every runtime predecessor.

## No.2 Make native open obey the NULL-plus-exception contract

### Code Change

On `fopen` failure, raise an owned OSError before returning NULL in both
`pcc/py_runtime/src/py_file.c` and its pcc-Python mirror. Keep the frontend's
post-call error check so control transfers to the active handler. The with-open
root remains entry-initialized because the error edge skips the binding store.

### CONFIRMED

The following focused command passed after the change:

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_python_exception_parity.py::test_return_from_failed_with_except_survives_outer_finally \
  tests/python/test_python_exception_parity.py::test_return_from_except_survives_finally \
  tests/python/test_native_file_open.py::test_native_file_runtime_round_trip
```

Observed result: `3 passed in 35.28s`. The investigation remains active until
the original current-pcc1 package test and broader required gates pass. That
test now reaches a second, independent prebuilt-Meson-overlay failure tracked
in [pcc1-existing-meson-output-requires-host.md](pcc1-existing-meson-output-requires-host.md).

## Report

No.2 fixed the actual control-flow contract: `py_file_open` now sets OSError
before returning NULL in both runtime mirrors, and the frontend checks that
exception edge. No.1 alone did not fix the bug, but its NULL initializer stays
because the newly real error edge bypasses the with-binding store while its GC
root registration is entry-wide. After the successor Meson/provenance fixes,
the original current-pcc1 package test passed in 6.65s.
