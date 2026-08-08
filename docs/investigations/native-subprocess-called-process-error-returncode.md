# Investigation: native subprocess failures lose CalledProcessError fields

## Status

resolved

## Problem Description

Under the self backend with `--python-libpython=off`, native lowering for
`subprocess.run(..., check=True)` and `subprocess.check_call(...)` raises a
generic runtime exception when the child exits nonzero. The generic exception
has no `returncode`, `cmd`, `output`, or `stderr` attributes.

This breaks ordinary Python exception handling and the self-host compiler
itself. `pcc/cli_bootstrap.py` catches `subprocess.CalledProcessError` and reads
`exc.returncode`; a pcc1 invocation whose compiled child exits nonzero
therefore crashes with `AttributeError: returncode` instead of forwarding the
child exit status.

The mode boundary is native Python frontend lowering, self backend,
no-libpython. Host CPython uses its own `subprocess` implementation and is not
affected.

## Repro

Focused current-source regression:

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_subprocess_no_libpython.py \
  -k preserves_called_process_error_fields
```

The compiled program runs `/bin/sh -c 'exit 7'` through
`subprocess.run(check=True)` and `/bin/sh -c 'exit 9'` through
`subprocess.check_call`, catches `subprocess.CalledProcessError`, and reads all
four public fields.

The self-host symptom is a pcc1 CLI invocation of a Python program that exits
nonzero: the child status should be the pcc1 process status, with no pcc1
traceback.

## Test [CONFIRMED]

The focused regression fails against current source after a successful strict
self/no-libpython compile. The first `subprocess.run(check=True)` child exits
7, the generic exception reaches the `except subprocess.CalledProcessError`
handler, and the first field read fails:

```text
AttributeError: returncode
1 failed, 1 deselected in 1.01s
```

This separates exception construction from exception matching: the handler is
entered, so the defect is the generic exception object's missing
`CalledProcessError` state.

## Proposals

- No.1 Instantiate the pcc-Python subprocess port's `CalledProcessError`
  directly from native lowering.
- No.2 Add subprocess-specific fields to the generic C runtime exception
  object.
- No.3 Disable the native `check=True` path and rely on unresolved fallback
  routing.

## No.1 Instantiate the pcc-Python CalledProcessError class

### Code Change

Use the closed-world export for
`pcc/py_stdlib/subprocess.py::CalledProcessError`, allocate that class through
the existing class runtime, and invoke its compiled `__init__` with the real
return code, original command object, and the output/stderr values supplied by
the native primitive. Raise the resulting user exception instance through the
normal TLS exception path.

This keeps high-level subprocess exception semantics in pcc-Python. It adds no
libpython edge and no subprocess-specific semantic object to the C-level
kernel.

The first implementation probe found that builtin-native imports are normally
excluded from recursive stdlib expansion. That is correct for modules whose
entire supported surface is lowering-only, but not for `subprocess`: its native
lowering now needs the semantic exception classes authored in the provider.
The closure therefore includes the `subprocess` provider while retaining
specialized process execution lowering.

### Status

Implemented and validated.

The compiled `CalledProcessError.__init__` export uses the same raw-int
scaffold ABI as its provider module. A focused export-shape regression prevents
the cross-module caller from boxing the status before a callee that already
boxes it for the Python field.

## Stacked Failure: raw POSIX wait status [CONFIRMED]

Fixing the exception object's shape exposed a distinct lower-level failure:
the `returncode` field existed, but contained 1792 for `exit 7` and 2304 for
`exit 9`. LLVM IR showed a matching `i64` caller/callee ABI and exactly one
`py_int_from_i64` in `CalledProcessError.__init__`; the same wrong values
appeared with both LLVM and self backends.

The numbers identify the second boundary precisely:

```text
1792 == 7 << 8
2304 == 9 << 8
```

`py_subprocess_run` returned `system()`'s encoded POSIX wait status directly.
The timeout path already normalized `waitpid()` status with
`WEXITSTATUS`/`WTERMSIG`, but the ordinary C and pcc-Python subprocess
substrates bypassed that contract.

The fix promotes that normalization to the shared C-kernel ABI
`py_process_normalize_wait_status`. The ordinary C substrate, the
pcc-Python substrate, and the timeout path now consume the same rule. This is
process/ABI machinery, not duplicated high-level subprocess semantics:
`CalledProcessError` remains authored in pcc-Python.

## No.2 Extend the generic C exception object

Rejected unless No.1 proves impossible. Adding a generic attribute dictionary
or subprocess-specific fields to `PyExceptionObject` expands the C semantic
runtime and duplicates fields already defined by the pcc-Python
`CalledProcessError` class.

## No.3 Disable native check=True

Unconfirmed and not selected. `subprocess` is also a native builtin import;
blindly declining the specialized lowering can route to CPython fallback or an
unsupported dynamic call rather than the pcc-Python port. That would violate
the no-libpython boundary unless the complete route were first proven.

## Report

Resolved. The strict self/no-libpython regression preserves all four
`CalledProcessError` fields and exact exit codes, the timeout and fallback
ratchets are green, and a final current-source pcc1 run forwards exit 7 without
`AttributeError` or traceback. See
`docs/goal/evidence/2026-07-28-native-subprocess-called-process-error.md`.
