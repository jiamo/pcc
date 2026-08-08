# Investigation: Freestanding Backend 3 copy-oldification

## Status

resolved

## Problem Description

The shared forwarding/identity and object-node/young-list substrates now have
strict pcc-Python owners, but Backend 3 copy-oldification orchestration still
lives in `py_gc_backend.py`.  It allocates an old copy, registers its object
node, installs forwarding, retires the young source, and handles rollback.

This slice moves that orchestration and the scalar-tag admission policy.  The
shared per-type payload copier remains one explicit cross-object ABI because
it also implements Backend 4 container/continuation/zpage behavior; moving it
belongs to the later relocation-copy slice.

## Repro

```bash
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_generational_oldification.py
```

## Test [CONFIRMED]

The initial owner test failed in 0.09 seconds because the strict owner did not
exist.  The first strict compile then failed closed on missing object-index
insert/remove cross-object signatures; adding those exact signatures closed
the boundary without weakening the validator.

## Proposals

- No.1 Move Backend 3 oldify-copy orchestration to strict pcc-Python [CONFIRMED]

## No.1 Move Backend 3 oldify-copy orchestration to strict pcc-Python

### Claim boundary

- strict pcc-Python owns Backend 3 scalar admission, copy registration,
  forwarding install, rollback, young unlink, and source inactivation;
- the managed backend calls one oldify ABI and no longer owns a duplicate;
- exact LLVM/self closure and production archive ownership are proven;
- focused remembered-child oldification/source-release gates stay green.

### Explicit non-claims

- shared per-type payload copying, promotion slot/root traversal, remembered
  owner draining, and the Backend 3 dispatcher remain open.

### CONFIRMED

- strict closure/archive gate: 5 passed in 66.03 seconds;
- three real pcc-Python oldification/source-lifetime gates passed in 62.24
  seconds;
- fresh no-libpython self-backed pcc1 completed in 85.823 seconds and compiled
  the real module with three definitions and no `py_cpy_*` calls.

## Report

Backend 3 scalar copy-oldification orchestration now has one strict
freestanding pcc-Python owner.  Promotion/root rewriting and the shared
per-type payload copier remain explicit subsequent slices.
