# PyTorch Claim Policy

Status: T0 claim-policy report. No PyTorch build, import, or runtime support is
implemented by this report.

This file exists to keep future PyTorch work separated into falsifiable modes.
It links to `docs/plans/pytorch.md` and should be updated only with concrete
commands and pass counts.

## Mode Tags

Every PyTorch narrative entry must use one of these mode tags:

- `strict-native`: `--python-libpython=off --backend self`; no CPython ABI
  extension artifacts and no `py_cpy_*` closure.
- `cpython-compat`: explicit CPython ABI compatibility mode; evidence is
  compatibility evidence only.
- `specialized`: strict-native plus pcc-owned typed/value/shape specialization
  after strict-native import already works.

## Allowed Claims

- "T0 claim hardening is complete: torch/pytorch special-case scanner is green."
- "`cpython-compat` install/import works for a named torch version with command
  and pass count."
- "`strict-native` fails with blocker X, and the blocker is covered by a test."
- "T6 gap analysis found N CPython C-API symbols and M C++ ABI requirements."

## Forbidden Claims

- "PyTorch is supported."
- "PyTorch on pcc works."
- "pcc-native `import torch` is done."
- "T_x is implemented" without mode tag, command, pass count, pcc1 result, and
  explicit non-claim paragraph.

## Current State

- PyTorch implementation status: not started.
- Generic prerequisites still missing: CMake package build driver, C++
  extension loader, pybind11 C-API shim, and pcc-native NumPy L6+.
- T0 constraint hardening owns only tests and documentation that prevent false
  claims and torch-specific compiler/runtime branches.
