# Investigation: multi-argument print leaks a LIFO GC root on exceptional operand evaluation

## Status

resolved

## Problem Description

While running the package-independent C-extension setitem regression for
`BUG-P0-NUMPY-IMPORT-SILENT-NULL-CALL-BLOCKS-L4-L5`, the current host compiler
reached native self-backend emission and rejected `main` with:

```text
self precise stack-map analysis in 'main': managed root state disagrees at
block join 'err.exit'
```

This is a new stacked compiler boundary, not the resolved Harness stale-pcc1
artifact-selection report.  `print("assign", storedemo.flags(matrix),
storedemo.total(matrix))` creates `pr.args.root` and enters it through
`pcc_gc_frame_enter_lifo`.  Nested attribute/call error predecessors branch
straight to the shared error exit, while only the successful print path emits
`pcc_gc_frame_leave_lifo`.  Precise stack-map analysis correctly refuses to
merge those inconsistent managed-root states.

## Reproduction

The first failing focused command is:

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_cext_setitem_dispatch.py
```

The test uses `--backend self --python-libpython=off`; on Darwin it explicitly
uses `PCC_SELF_LINK=cc` only for the native-extension final-link oracle.  The
failure occurs before execution, during precise stack-map analysis of the
current generated IR.

## Root Cause

`pcc/py_frontend/codegen/print_lowering.py::_emit_print_many` allocates the
argument tuple, stores it in an entry-block slot, and emits a LIFO frame enter.
It does not redirect pcc or CPython operand-error edges through a matching
cleanup block.  Only the success tail leaves the root.  The same success tail
also does not release the tuple's original owned reference after
`py_print_many`, which borrows the tuple.

The splat form has the same root-lifetime shape after
`py_call_merge_posargs`, so the implementation and regression must cover both
fixed positional arguments and the native splat/`sep`/`end` path.  Generator
print arguments use a persistent heap-frame slot rather than this stack LIFO
slot and remain a separate lifetime contract.

## Accepted Fix Boundary

- Root the non-generator print tuple through the existing managed temporary
  root protocol.
- While print operands and `sep`/`end` expressions are evaluated, redirect
  both pcc and CPython exceptional edges through a cleanup that leaves the
  exact active LIFO root and releases its owned tuple once.
- Restore the surrounding handler targets after lowering the print call, so a
  caught operand exception remains catchable instead of being forced to the
  function epilogue.
- Leave and release once on success as well.
- Add a minimal self-backend regression with an exception raised by a later
  print operand inside `try`, plus the equivalent splat/keyword lifetime edge.

Do not weaken precise stack-map joins, suppress the diagnostic, special-case
the NumPy test, or reuse the resolved Harness stale-compiler explanation.

## Result

The accepted fix boundary is implemented and verified.  Fixed and splat print
operand failures now reach their source-level handlers after balanced root
cleanup, precise stack-map emission succeeds, and both pcc-Python and C-oracle
C-extension setitem regressions pass.  Evidence is recorded in
`docs/goal/evidence/2026-08-14-print-lifo-exception-unwind.md`.
