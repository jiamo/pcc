# Investigation: Freestanding Backend 3 remembered-owner queue

## Status

resolved

## Problem Description

Backend 3 scalar copy-oldification already has a strict pcc-Python owner, but
the old-to-young write-barrier queue, allocation-failure overflow fallback,
and budgeted drain still lived in the managed GC policy module.  This slice
moves that queue policy without importing Backend 4 store-buffer/zpage
behavior.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_generational_remembered_owners.py
```

## Test [CONFIRMED]

The initial owner test failed in 0.10 seconds because the strict source did not
exist.  The first strict compile then failed closed because `% 16` generated
checked modulo exception machinery.  Replacing it with the equivalent
`processed & 15` cadence preserved the every-16-owners safepoint and removed
the managed exception dependency.

## Proposals

- No.1 Move the Backend 3 remembered-owner queue to strict pcc-Python
  [CONFIRMED]

## No.1 Move the Backend 3 remembered-owner queue to strict pcc-Python

### Claim boundary

- strict pcc-Python owns queue head access, enqueue, clear, overflow scan, and
  budgeted drain;
- the managed write barrier/reset/step call exact ABIs and contain no duplicate
  queue implementation;
- exact LLVM/self closure, archive ownership, and real remembered-child gates
  are proven.

### Explicit non-claims

- promotion slot/root traversal, TLS/root rewriting, the Backend 3 dispatcher,
  shared payload copying, and Backend 4 relocation remain open.

### CONFIRMED

- strict closure/archive gate: 5 passed in 62.53 seconds;
- five real remembered-child/write-barrier gates passed in 133.47 seconds;
- fresh no-libpython self-backed pcc1 completed in 34.301 seconds and compiled
  the real module with six definitions and no `py_cpy_*` calls.

## Report

Backend 3 remembered-owner queue policy now has one strict freestanding
pcc-Python owner.  Promotion/root rewriting is the next isolated Backend 3
slice.
