# Investigation: freestanding tracing sweep collector

## Status

resolved

## Problem Description

The strict freestanding GC objects now own common tracing mark completion and
PASS-1 candidate-aware referent clearing, but `py_gc_backend.py` still owns the
surrounding collector kernel: sweep-candidate discovery, PASS-0 finalizer
dispatch, PEP 442 resurrection re-mark, and PASS-2 type-specific deallocation.
Keeping those four phases managed leaves the production GC1..4 sweep boundary
split across two objects and keeps the complete deallocator closure inside the
large backend-policy object.

This slice moves exactly those four raw phases.  Public config-aware wrappers
(`pcc_gc_has_tracing_sweep` / `pcc_gc_collect_tracing`) continue to live in
`py_gc_backend.py` and call the strict raw exports.  Incremental/concurrent,
generational and relocating policy remains outside the slice.

Predecessor:
`docs/investigations/freestanding-gc-tracing-sweep-slot-actions.md`.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_tracing_sweep_collector.py
```

Expected pre-change result: `freestanding_gc_tracing_sweep_collector.py` is
absent and all four raw phases are still defined in `py_gc_backend.py`.

## Test [CONFIRMED]

The pre-change focused gate fails because the strict collector source is
absent.  The completed gate will require one strict source/archive owner, exact
LLVM/self closure, PASS-0 -> resurrection recheck -> PASS-1 -> PASS-2 ordering,
the existing pin/fresh/extension-type guards, delayed backend-4 zpage freeing
notification and managed public wrappers that cross only the raw ABI.

## Proposals

- No.1 Move the common tracing sweep kernel into one strict object [CONFIRMED]

## No.1 Move the common tracing sweep kernel into one strict object

### Code Change

Create `freestanding_gc_tracing_sweep_collector.py` with raw exports for
candidate discovery, final deallocation, resurrection recheck and bounded
two-phase sweep.  Consume the strict mark and clear phases through exact extern
signatures.  Replace the four managed definitions with extern aliases without
moving configuration, scheduling or backend policy.

### CONFIRMED

The strict object uniquely owns all four raw phases and has the same exact
undefined closure under LLVM and self emission.  The managed backend retains
only raw extern aliases for candidate discovery and bounded sweep.  Structural
tests pin PASS-0 -> resurrection recheck -> PASS-1 -> PASS-2 order, pin/fresh
and C-extension guards, logical-death publication, and delayed backend-4 zpage
notification.  Production archive ownership, five-backend object-lifetime,
weakref/finalizer/resurrection contracts and a fresh no-libpython/self pcc1 are
green.

## Report

No.1 landed without moving configuration-aware public wrappers or any backend
scheduling/promotion/relocation policy.  Evidence:
`docs/goal/evidence/2026-08-03-freestanding-gc-tracing-sweep-collector.md`.
