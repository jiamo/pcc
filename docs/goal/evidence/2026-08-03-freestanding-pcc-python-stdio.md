# Freestanding pcc-Python stdio subset

Date: 2026-08-03

Task: `LIBC-P2-STDIO-SUBSET`

## Claim boundary

In Darwin arm64, self-backend, no-libpython runtime mode, the production
pcc-Python runtime archive now owns exactly the stdio subset used by pcc:
`remove`, `fopen`, `fclose`, `fread`, `fwrite`, `fflush`, `ferror`, `fgetc`,
`fprintf`, `snprintf`, `vsnprintf`, `__stderrp`, `popen`, and `pclose`.

This is not a general POSIX `FILE` implementation claim.  The owned ABI uses
the generated `pcc_stdio_abi.h` layout and the already-verified freestanding
allocator, raw-memory, IO, filesystem, process, and spawn closures.  musl and
apple-libc remain read-only semantic/layout references; no musl stdio C object
is linked into the production archive.

## Compiler/bootstrap repairs required by the real module

- C-varargs IR postprocessing now scans definitions deterministically instead
  of using a callable-regex shape that pcc1 could not reproduce.
- `@c_abi_variadic_export` is propagated into the scaffold `FunctionType`.
- Native `bool` values crossing the scaffold object-handle ABI are boxed with
  `py_bool_from_bit`; an `i1 true` can no longer become pcc tagged integer zero.
- Freestanding modules explicitly reject compiler-injected threads-on
  safepoints.  They are the runtime dependency root and may not call back into
  the managed thread/GC substrate.

## Focused semantic and structural gates

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_stdio.py \
  tests/python/test_freestanding_variadic_export.py \
  tests/c/test_c_varargs_split.py \
  tests/python/test_freestanding_module.py
```

Result: `39 passed in 11.77s`.

The suite differentially covers file lifecycle, buffering, EOF/error state,
partial writes, formatting (including width/precision/float and native
variadics), flush/close ordering, `remove`, and `popen`/`pclose` child status.
Both LLVM-object and self-backend-object consumers are exercised.

The wider scaffold/variadic suite reports `76 passed`.  The compiled
bootstrap-facing `pcc_multi + pipeline` toy-module smoke reports
`1 passed in 89.90s`.

## Fresh pcc1 evidence

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/libc-stdio-stage1-v5-profile \
  bash scripts/bootstrap.sh --out-dir build/libc-stdio-stage1-v5 \
  --backend self --stage 1
```

Result: publication green, `elapsed_ms=34784`.

That fresh self-backend/no-libpython pcc1 compiled the real module:

```text
PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  build/libc-stdio-stage1-v5/pcc1 --ir-scaffold=on --backend self \
  --python-libpython off --python-library \
  --emit-llvm=build/libc-stdio-stage1-v5/freestanding-stdio-pcc1.ll \
  pcc/py_runtime/py/freestanding_stdio.py
```

Result: exit 0 in 0.77 seconds.  The IR defines C-variadic `snprintf` and
`fprintf`, contains ten native `va_arg` instructions, and contains zero
`__pcc_va_arg_*` placeholders.

`test_bootstrap_gate_baseline.py` plus the default import ratchet report
`3 passed, 2 deselected in 17.67s`.

## Link ownership and import ratchets

`nm -A pcc/py_runtime/libpy_runtime_pcc_py.a` attributes all fourteen owned
symbols only to `freestanding_stdio.o`.  `py_file.o` remains a Python file
semantic consumer and imports the owned ABI; it does not define a competing C
stdio implementation.  The archive contains no musl/vendor stdio object.

A fresh Darwin stage1 removes the fourteen former libSystem stdio imports and
adds only four lower OS ABI entries required by the same behavior:
`unlinkat`, `pipe`, `posix_spawn_file_actions_addclose`, and
`posix_spawn_file_actions_adddup2`.  The exact threads-off set is 46/46 against
`tests/libc_import_baseline.json` (previously 56).  The exact threads-on set is
52/52 against `tests/libc_import_baseline_threads.json`; its only delta from
threads-off is the six named pthread mutex/condvar entries.

The faithful threads-on integration ratchet reports
`1 passed in 72.55s`.  An intermediate retained-path diagnostic produced an
invalid startup recursion because its copied source root was not named
`py_runtime`; the exact original test shape is green and no production change
was made for that diagnostic artifact.

## Remaining boundary

This slice is `DONE_WEAK`, not `DONE_STRONG`: run the final shared
pcc1 -> pcc2 -> pcc3 fixed-point and five-GC acceptance matrix once after the
adjacent libc/GC migrations are complete.  No general stdio/POSIX completeness
is claimed.
