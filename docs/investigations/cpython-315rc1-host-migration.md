# Investigation: migrate pcc's default host to CPython 3.15.0rc1

## Status

resolved

## Problem Description

The user requires pcc to track the newest Python and explicitly directs the
repository's host Python to move from CPython 3.13.2 to CPython 3.15.0rc1.
This is a direct migration, not an optional side matrix.  The no-libpython
pcc1 ownership boundary remains unchanged: host 3.15 is the pcc0 builder and
oracle, not a runtime fallback for pcc1.

The current project `.venv` is CPython 3.13.2.  CPython 3.15.0rc1 is not
installed locally.  `pyproject.toml` permits Python >=3.13, but the lock pins
`llvmlite==0.46.0` and currently records wheels only through CPython 3.14, so
dependency compatibility must be resolved explicitly rather than hiding a
3.13 interpreter behind `uv run`.

## Repro

```text
.venv/bin/python --version
Python 3.13.2

python3.15 --version
command unavailable
```

Required result: repository-default `uv run python` and `.venv/bin/python`
report exactly 3.15.0rc1, the locked toolchain imports and focused host/compiler
gates pass, and build receipts name the 3.15 executable/runtime identity.

## Test [CONFIRMED]

The version mismatch above is directly observed.  Dependency and compiler
compatibility tests remain pending until the new interpreter is installed.

## Proposals

- No.1 Install 3.15.0rc1 and atomically replace the project environment [CONFIRMED]

## No.1 Install 3.15.0rc1 and atomically replace the project environment

### Code Change

The local uv 0.10.2 download catalogue predated CPython 3.15rc1.  It was
checksum-verified and upgraded to uv 0.12.7, whose managed catalogue contains
3.15.0rc1.  The exact managed interpreter is pinned in `.python-version` and
the final `.venv` was created in place from it; the old 3.13.2 environment is
only a recoverable build artifact and is not on the project execution path.

MLX has no cp315 wheel or sdist and is not imported by pcc core, so it moved to
an explicit `mlx` extra that fails clearly when requested on an unsupported
interpreter.  llvmlite 0.46's build hook is incompatible with Python 3.15;
llvmlite 0.47 builds from sdist against LLVM 20 and passes a parse/verify probe.
The host/oracle identity moved to CPython 3.15.0rc1 while pcc1's honest language
baseline remains 3.13 until the separate compatibility task proves more.

### CONFIRMED

```text
uv run python -VV
Python 3.15.0rc1 ... [Clang 22.1.3]

C parser                              44 passed
llvm_capi end-to-end                   7 passed
Python multi-file                     41 passed in 101.91s
replacement contract/workloads       47 passed
```

A source-frozen GC0 Stage1 ran under the signed 3.15 Tachyon profiler and
published a complete build receipt:

```text
artifact             build/host-python-315rc1-stage1-profile-v2
host cache tag       cpython-315
host version         3.15.0rc1 candidate 1
pcc1 SHA-256         09d131f88355d1e33e1f020e291c5830d4499cc08fa99fc920edd3dd8646f0e8
linkage              libSystem only
profiled wall/CPU    354.92s / 1212.90s
```

The profiled wall is diagnostic, not the uninstrumented Stage1 performance
baseline.  Tachyon captured 227 process flamegraphs; the aggregate receipt is
`build/host-python-315rc1-tachyon-v2/aggregate.json`.

## Report

The repository-default host and oracle are now exact CPython 3.15.0rc1.  No
3.13/3.14 interpreter or package directory participates in `uv run`, Stage1,
or the focused gates.  This closes host migration only; it does not relabel
pcc1's current language subset as Python 3.15 compatibility.
