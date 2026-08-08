# Investigation: Freestanding Backend 3 root scheduler

## Status

resolved

## Problem Description

Backend 3 oldification, remembered owners, slot promotion, and stable-root
rewriting had strict owners, but TLS exception-root orchestration and the
budgeted young-list step remained in the managed policy module.  This slice
moves the Backend 3 scheduler without prematurely moving the five-backend
dispatcher whose Backend 4 relocation branch is still open.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_generational_scheduler.py
```

## Test [CONFIRMED]

The initial owner test failed in 0.09 seconds because the strict source did not
exist.  The first strict compile rejected native TLS get/set because those
boundaries lacked exact strict signatures.  Registering only `() -> ptr` and
`(ptr) -> void` closed the boundary without allowing a symbol prefix.

## Proposals

- No.1 Move Backend 3 TLS/root scheduling to strict pcc-Python [CONFIRMED]

## No.1 Move Backend 3 TLS/root scheduling to strict pcc-Python

### Claim boundary

- strict pcc-Python owns frame/TLS promotion order, remembered drain, budgeted
  young-list traversal, retry, and safepoints;
- minor refill and public dispatch consume one strict Backend 3 step ABI;
- exact LLVM/self closure, archive ownership, and real scheduler gates are
  proven.

### Explicit non-claims

- the five-backend dispatcher, shared write-barrier/payload policy, Backend 4
  relocation, and final no-C-GC proof remain open.

### CONFIRMED

- strict closure/archive gate: 5 passed in 63.67 seconds;
- four real pcc-Python scheduler gates passed in 118.69 seconds;
- fresh no-libpython self-backed pcc1 completed in 35.263 seconds and compiled
  the real module with three definitions and no `py_cpy_*` calls.

## Report

Backend 3 TLS/root scheduling now has one strict freestanding pcc-Python
owner.  Work proceeds to the Backend 4 relocation family before the shared
dispatcher can be moved without a policy cycle.
