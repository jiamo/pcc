# Investigation: native subprocess provider omitted from shallow multi-file closure

## Status

resolved

## Problem Description

The resolved
[`native-subprocess-called-process-error-returncode.md`](native-subprocess-called-process-error-returncode.md)
change made native `subprocess.run(..., check=True)` and
`subprocess.check_call(...)` construct the pcc-Python
`subprocess.CalledProcessError`. That lowering emits link-time references to
the provider class and its compiled `__init__`.

`compile_python_multi(..., recursive_stdlib=False)` intentionally keeps an
explicit multi-file source set shallow. Its closure therefore omitted
`pcc/py_stdlib/subprocess.py` even when an admitted module imported
`subprocess` and native lowering emitted hard references to that provider.
The bootstrap-facing `pcc_multi.py + pipeline.py` pair then failed to link.

This is a stacked closure regression, not a failure of the preserved
`CalledProcessError.returncode` semantics. The repeated macOS target-triple
messages are warnings; the terminal undefined-provider symbols are the failing
boundary.

## Repro

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_compiled_pcc_multi_can_compile_toy_module
```

Expected: the explicit bootstrap pair links, compiles the toy module under
`--python-libpython off`, and the toy prints its expected output.

Observed:

```text
Undefined symbols for architecture arm64:
  "_.class.subprocess.CalledProcessError"
  "_user_subprocess_CalledProcessError___init__"
pcc_multi: clang link failed (exit 1)
1 failed in 65.45s
```

## Test [CONFIRMED]

The existing bootstrap-pair E2E is the public-interface regression. It passes
only `scripts/pcc_multi.py` and `pcc/py_frontend/pipeline.py` as explicit
sources, so it proves the compiler supplies required provider dependency edges
without callers or tests manually naming implementation providers.

## Proposals

- No.1 Admit required native semantic providers independently of recursive
  stdlib expansion [CONFIRMED]
- No.2 Enable full recursive stdlib expansion for every pcc_multi invocation
  [DENIED]
- No.3 Restore a generic C exception for shallow multi-file builds [DENIED]

## No.1 Admit required native semantic providers independently

### Code Change

Add a focused closure pass for native builtin imports whose lowering requires
compiled semantic providers. Run it for every multi-file closure after scaffold
filtering, regardless of the optional `recursive_stdlib` flag. Append only the
pcc-owned provider modules required by admitted imports; retain full recursive
stdlib discovery as a separate opt-in expansion.

### CONFIRMED

The multi-file closure now scans admitted sources, including deferred function
bodies, for native builtin imports whose lowerings require compiled semantic
providers. It adds only those mandatory pcc-owned providers before the optional
recursive stdlib pass.

The original E2E changed from the confirmed undefined-symbol failure to:

```text
1 passed in 97.53s
```

The full bootstrap-shim file passed all 93 tests, the separate multi-file
compile file passed 40 tests, and the shallow/recursive stdlib regressions
passed 37 tests. Callers still provide only their intended explicit modules.

## No.2 Enable full recursive stdlib expansion

### DENIED

`recursive_stdlib=False` is the documented shallow multi-file contract.
Enabling the complete host/pcc stdlib walker to satisfy one mandatory link edge
would change closure scope and compile cost for unrelated explicit builds.

## No.3 Restore a generic C exception

### DENIED

That would remove the `CalledProcessError` fields fixed by the predecessor
investigation and move high-level subprocess semantics back into the C runtime.

## Report

Proposal No.1 is confirmed. Required native semantic-provider edges are now a
compiler closure invariant independent of optional recursive stdlib expansion.
The bootstrap pair links without manually naming `pcc/py_stdlib/subprocess.py`,
while the original strict no-libpython `CalledProcessError` and pcc1 exit-code
gates remain green.
