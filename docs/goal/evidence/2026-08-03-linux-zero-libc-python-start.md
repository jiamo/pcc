# Linux x86_64 zero-libc pcc-Python tracer

Date: 2026-08-03

Task: `LIBC-P3-LINUX-ZERO-LIBC-TRACER`

## Claim boundary

In host-pcc0 Python-frontend, x86_64 Linux self-backend, no-libpython mode,
`pcc/py_runtime/py/freestanding_linux_start.py` produces a statically linked
ELF executable with a pcc-Python-authored `_start`, initial-stack decoding,
raw `write`, and raw `exit_group` path.  The production link contains exactly
one object derived from that Python source through generated self-backend
assembly.  It contains no hand-written C/assembly startup object, libc, C
runtime object, dynamic interpreter, dynamic dependency, or undefined symbol.

This proves only the Linux x86_64 tracer.  It is not the full runtime, full C
frontend, five-GC, or Darwin zero-boundary claim.  Darwin intentionally keeps
its named libSystem ABI.  pcc1 currently has no cross-target CLI option, so the
executing Linux artifact is not mislabeled as a pcc1 cross-compile.

## Owned process-entry contract

The self backend accepts only global `void @_start(ptr initial_stack)`.  It
preserves the kernel's original stack pointer, establishes SysV call alignment,
stores the pointer as the pcc-Python argument, and emits `ud2` if the entry ever
returns instead of terminating with `process_exit`.

The pcc-Python source reads `argc` at stack offset 0 and `argv[0]` at offset 8,
writes `pcc zero-libc ok\n`, and exits through Linux `exit_group`.  Ambiguous
`_start` signatures fail closed.  Ordinary functions retain their existing
SysV argument/return behavior.

## Docker artifact gate

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 -m integration \
  tests/integration/test_self_backend_x86_64_linux.py::\
test_linux_x86_64_freestanding_python_start_is_static_zero_libc
```

Final result: `1 passed in 26.81s`.

Observed artifact evidence:

- `file`: ELF 64-bit x86-64, statically linked;
- `readelf -l`: no `PT_INTERP`;
- `readelf -d`: no `DT_NEEDED` and no dynamic section;
- `nm -u`: empty;
- link map `LOAD` objects: only
  `/tmp/pcc_zero_libc.from_python.o`;
- defined `_start` symbol present;
- execution exit code 0 and stdout exactly `pcc zero-libc ok\n`.

The adjacent original `main` and syscall6 Docker gates also passed:
`3 passed in 18.94s` including this tracer.

## Compiler and self-host evidence

The real CLI had two target propagation paths.  Both now pass the explicit
target to `compile_python`; multi-file emission applies the same target to
every module.  The 61-test focused regression batch covering CLI, observability,
x86 backend, freestanding process entry, and memory/string exports reports
`61 passed in 9.48s`.

A fresh current-source self-backend/no-libpython stage1 pcc1 was published:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=57176 \
  output=build/libc-linux-tracer-stage1-v2/pcc1
```

That pcc1 compiled both the tracer and memory/string freestanding modules in
library/no-libpython mode.  Its IR preserves:

```text
define external void @_start(ptr %.1)
define external void @bzero(ptr %.1, i64 %.2)
define external void @explicit_bzero(ptr %.1, i64 %.2)
```

This pcc1 evidence closes the independently compiled `NoneType` ABI drift; it
does not claim pcc1 Linux cross-target execution.
