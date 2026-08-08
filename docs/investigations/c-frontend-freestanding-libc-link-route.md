# Investigation: C frontend has no shared pcc-Python libc link route

## Status

resolved

## Problem Description

`LIBC-P2-C-FRONTEND-FREESTANDING-LIBC` requires C programs compiled by pcc to
resolve the supported libc ABI from the same strict pcc-Python objects used by
`libpy_runtime_pcc_py.a`.  The C CLI currently exposes only its ordinary
runtime path and `--system-link`; neither mode selects that archive or labels
the host-libc versus freestanding boundary.  Linux additionally needs a
pcc-Python startup object that initializes the environment, calls the C
program's `main`, and exits through the raw process substrate.

## Repro

Run the minimized public CLI test:

```bash
gtimeout 45s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_c_freestanding_libc_link.py
```

Expected: the C program links against the shared pcc-Python allocator and
mem/string implementation and exits 0.  Current result: exit 1 from pytest
because `cli_main` returns 2 and reports
`Error: unknown option: --freestanding-libc`.

## Test [CONFIRMED]

`tests/c/test_c_freestanding_libc_link.py::test_c_cli_freestanding_libc_runs_pcc_python_mem_and_allocator`
failed deterministically on 2026-08-04 with the unknown-option error above.

## Proposals

- No.1 Add an explicit shared freestanding-libc link mode [CONFIRMED]

## No.1 Add an explicit shared freestanding-libc link mode

### Code Change

Add a C-only `--freestanding-libc` CLI mode which implies native system
link/run, obtains the existing production pcc-Python runtime archive through
the same freshness/build path as the Python frontend, and places it before
platform libraries so supported libc references resolve from pcc-Python.
Keep the ordinary `--system-link` behavior unchanged.  On Darwin, retain and
label the libSystem machine boundary.  On Linux, add a strict pcc-Python
`_start` module that reconstructs `argc`/`argv`/`envp`, initializes the
freestanding environment, calls C `main`, and exits through the raw syscall
substrate; link it statically with no interpreter or dynamic dependencies.
Add source-attribution/link-map ratchets so no selected libc implementation
comes from host libc, retained handwritten C semantics, or vendored musl.

### CONFIRMED

The public `--freestanding-libc` route is C-only and implies a final system
link.  Darwin selects `libpy_runtime_pcc_py.a` through the Python frontend's
normal freshness path and retains the explicit libSystem machine boundary.
Linux x86_64 compiles the same strict pcc-Python libc modules for the Linux
target, adds the pcc-Python `_start`, and links with
`-nostdlib -static -no-pie -Wl,-e,_start`.

The first full memory/string differential exposed an independent source-level
LVN bug in repeated character-array initialization.  That failure and its fix
are recorded separately in
[`c-lvn-array-string-initializer-reuse.md`](c-lvn-array-string-initializer-reuse.md).

Confirmed gates on 2026-08-04:

```text
56 passed in 7.03s
  public C route, link ownership, startup ABI, full portable mem/string
  differential, freestanding closure, LVN and x86 self-backend regressions

1 passed in 4.67s
  real zlib freestanding route

1 passed in 36.24s
  real SQLite freestanding route

1 passed in 9.62s
  Linux x86_64 static/readelf/nm/link-map Docker gate

2 passed in 61.52s
  current-source compiled pcc_multi bootstrap-facing helper and AST reuse
```

The Darwin link map selects the pcc-Python allocator and memory/string archive
members.  Its only SDK-owned symbol entries for the focused consumer are
`_mmap.got` and `_munmap.got`, owned by `libsystem_kernel.tbd` under the
`libSystem.tbd` umbrella.  This is explicitly a Darwin libSystem boundary,
not a zero-libc claim.

## Report

Proposal No.1 landed as the finite shared link route.  Ordinary C linking is
unchanged; callers must opt in with `--freestanding-libc`.  The public route
fails closed for Python inputs, emit-only modes, and requests for a C-authored
semantic runtime.  Linux proves a static zero-host-libc artifact for the
supported closure; Darwin proves the narrower enumerated libSystem boundary.
No vendored musl object or second handwritten C libc implementation is linked.
