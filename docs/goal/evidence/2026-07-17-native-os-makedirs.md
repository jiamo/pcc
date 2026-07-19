# Native no-libpython `os.makedirs`

Date: 2026-07-17

Task: `AUD-P1-NATIVE-OS-MAKEDIRS`

## Generic runtime and lowering owner

`os.makedirs(path, exist_ok=...)` now lowers through the generic
`py_os_makedirs` runtime ABI instead of a `py_cpy_*` bridge:

- `pcc/py_frontend/codegen/native_os.py` recognizes the one-path-argument
  call, evaluates the optional `exist_ok` keyword with Python truthiness, calls
  `py_os_makedirs`, and takes the normal post-call exception edge.
- `pcc/py_frontend/codegen/runtime_abi.py` declares the object/path plus `i32`
  ABI, and `pcc/py_runtime/include/py_runtime.h` exposes the runtime entrypoint.
- `pcc/py_runtime/src/py_os_path.c` recursively creates path components and
  preserves the final-component `exist_ok` behavior.
- `pcc/py_runtime/py/py_os_path.py` mirrors the same algorithm and error
  contract for the self-hosted pcc-Python runtime.

No package-name special case or libpython fallback was added.

## Focused regression

`tests/python/test_native_os_makedirs.py` proves all three finite boundaries:

- emitted LLVM IR contains `call ptr @py_os_makedirs` and contains zero
  `py_cpy_*` fallback calls under `--python-libpython=off`;
- the C semantic runtime creates a nested directory, accepts an existing final
  directory with `exist_ok=True`, and raises `OSError` with `exist_ok=False`;
- the pcc-Python runtime mirror passes the same executable behavior.

Gate:

```bash
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_os_makedirs.py
```

Result: `3 passed in 1.18s`.

## Bootstrap blockers and gate-cost correction

The first five-GC attempt exposed a shared self-host codegen defect rather than
an `os.makedirs` defect: handler exception state could leak across LLVM
function owners, causing contextual codegen workers to render empty modules.
The generic correction owner-labels active handler entries and accepts them
only for the current LLVM function. The native emit worker also normalizes a
missing target triple, and the frontend worker result reader now rejects a
zero-byte module immediately with its module name instead of deferring failure
to a large undefined-symbol link.

The cold matrix additionally exposed a harness scheduling race: GC4/GC3 could
acquire the sole warm-up lease before GC0 published its waiting file. The gate
now reserves that lease for reference backend GC0 until GC0 is terminal, then
admits three challenger chains concurrently. Its focused resource-control
tests pass (`2 passed, 15 deselected in 0.24s`). This changes gate scheduling,
not compiler or GC semantics.

An initial cold matrix invocation timed out at 700 seconds after completing
GC4 and GC0 stage2; no final summary was claimed, and no child processes
survived. One 86-second continuation was intentionally interrupted after the
lease race was proven; it was likewise not counted as evidence. The final
content-addressed continuation used the required command and produced a full
summary:

```bash
gtimeout 700s env -u LC_ALL uv run pytest -q \
  tests/python/gc/test_pcc_bootstrap_full_gc*.py
```

Result: `5 passed in 436.45s (0:07:16)`.

## Required fallback ratchet

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py
```

Result: `24 passed in 242.90s (0:04:02)`. A prior 180-second attempt reached
15 tests and timed out without a summary; it was discarded and left no child
processes.

## Claim boundary

This proves native no-libpython lowering and executable behavior for
`os.makedirs(path)` and `os.makedirs(path, exist_ok=...)` in both the C and
pcc-Python runtime layers, plus the current-source self-backend pcc1/pcc2/pcc3
fixed-point gate under GC0..4. It does not claim full `os` compatibility,
arbitrary path-like protocol completeness, permission-mode parity, Windows
path semantics, or elimination of the low-level C runtime kernel.
