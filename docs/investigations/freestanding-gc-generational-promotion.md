# Investigation: Freestanding generational promotion and stable roots

## Status

resolved

## Problem Description

Backend 3 oldification and remembered-owner queue policy had strict owners,
but slot promotion, recursive referent traversal, and stable frame-root cache
rewriting still lived in the managed policy module.  Backend 4 reuses the same
slot promotion adapter, so the slice had to preserve its zpage generation
notification without duplicating the object-slot walker.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_generational_promotion.py
```

## Test [CONFIRMED]

The initial owner test failed in 0.09 seconds because the strict source did not
exist.  The first strict compile rejected unnamed helper functions; giving the
finite callbacks/helpers explicit C ABI names retained fail-closed behavior.
The second compile rejected the prior oldification export because it lacked a
strict cross-object signature; exact signatures for the three existing
oldification exports fixed the boundary without adding a prefix wildcard.

## Proposals

- No.1 Move shared generational slot promotion to strict pcc-Python
  [CONFIRMED]

## No.1 Move shared generational slot promotion to strict pcc-Python

### Claim boundary

- strict pcc-Python owns owned/borrowed deep/shallow promotion, recursive slot
  traversal, stable-root classification, and cached frame-slot rewriting;
- one strict object-slot walker remains the type-coverage owner;
- Backend 4 promotion still updates zpage generation through one exact ABI;
- exact LLVM/self closure, archive ownership, mapped roots, and real GC3 roots
  are proven.

### Explicit non-claims

- TLS exception-root orchestration, the Backend 3 dispatcher, shared payload
  copying, and Backend 4 relocation remain open.

### CONFIRMED

- strict closure/archive gate: 5 passed in 63.44 seconds;
- six real pcc-Python root/slot promotion gates passed in 63.29 seconds;
- mapped-root closure/runtime gate: 5 passed in 18.21 seconds;
- migrated source-shape gates: 14 passed;
- fresh no-libpython self-backed pcc1 completed in 77.356 seconds and compiled
  the real module with ten definitions and no `py_cpy_*` calls.

## Report

Shared GC3/GC4 promotion and stable-root rewriting now have one strict
freestanding pcc-Python owner.  TLS/root orchestration and the Backend 3 step
dispatcher are the remaining GC3 policy slice.
