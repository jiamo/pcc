# Investigation: os.makedirs mode falls back to a no-libpython stub

## Status

resolved

## Problem Description

The current-pcc1 Harness passed its reactive Cordis lifecycle self-check but failed the durable Session integration before opening the log. `identity_runtime._persist_first_writer` called `os.makedirs(..., mode=0o700, exist_ok=True)`, and strict no-libpython lowering replaced the whole function with an unavailable-function stub.

The native `os.makedirs` lowering accepted only one positional path and an optional `exist_ok` keyword. PCC's C and pcc-Python runtimes also exposed no mode parameter and always created every component with `0777`. The Harness needs the standard mode argument so its per-home identity directory is owner-only without importing CPython.

## Repro

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_harness_session_composition.py::test_current_pcc1_persist_resume_and_fork \
  -m integration
```

Expected: three native Harness processes persist, resume, and fork one Session while keeping one anonymous identity. Observed 2026-08-14: the first process raised `NotImplementedError: no-libpython function unavailable: identity_runtime._persist_first_writer`.

Setting `PCC_DEBUG_STRICT_NOLIB_STUB=identity_runtime._persist_first_writer` localized the first fallback call to `py_cpy_import("os")`; libpython-enabled IR showed that `os.makedirs` with `mode` was the triggering operation.

## Test [CONFIRMED]

`tests/python/test_native_os_makedirs.py` already covers recursive creation and `exist_ok`. The revised test also supplies `mode=0o700` and checks the leaf directory permission. The durable Session integration remains the current-pcc1 product gate.

## Proposals

- No.1 Carry mode through native lowering and both runtimes [implemented]

## No.1 Carry mode through native lowering and both runtimes

### Code Change

Accept the standard positional or keyword `mode` and `exist_ok` forms, pass mode through the native ABI, create intermediate components with `0777`, and apply the requested mode to the leaf component. Keep duplicate or unknown keyword forms on the existing general call path so Python argument diagnostics remain authoritative.

### Validation

The native makedirs regressions passed as part of a 5-test native JSON/filesystem/file-iteration group. Current pcc1 self-bootstrap and strict no-libpython Harness compilation passed. The durable Session integration created the Harness home, reused its identity, and persisted, resumed, and forked Session logs across three native processes.
