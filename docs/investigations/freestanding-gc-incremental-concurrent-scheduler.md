# Investigation: freestanding incremental/concurrent GC scheduler

## Status

resolved

## Problem Description

The common mark/sweep kernel and public config/collection boundary now have
strict freestanding pcc-Python owners, but backend 1/2 pacing and scheduling
remain embedded in the managed `py_gc_backend.py`.  The remaining cluster owns
allocation debt, budget calculation, bounded tracing steps, debt discharge,
pause metrics, backend-1 auto-step, backend-2 CMS accounting and the public
allocation notification entrypoint.

This slice moves that finite backend-1/2 scheduling closure.  The public
`pcc_gc_step` dispatcher remains managed for its backend-3 promotion and
backend-4 relocation branches, but delegates backend 1/2 to one strict raw
entrypoint.  Generational and relocating policy are explicitly out of scope.

Predecessor:
`docs/investigations/freestanding-gc-public-collection-config.md`.

Behavioral references:
`docs/investigations/gc-backend1-incremental-tricolor-pacer.md`,
`docs/investigations/gc-backend1-auto-step-sweep-debt.md`, and
`docs/investigations/gc-backend2-concurrent-mark-worker.md`.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py
```

Expected pre-change result: the strict scheduler source is absent and all
eleven scheduling symbols or helpers remain in `py_gc_backend.py`.

## Test [CONFIRMED]

The first focused node failed because the strict scheduler source was absent.
After adding the source, the first strict compile failed closed on a
`py_exc_new` reference emitted by ordinary Python floor division.  Replacing
only the proven-positive constant-divisor operations with the raw
`unsigned_div_i64` intrinsic made the strict compile green without weakening
the validator.

## Proposals

- No.1 Move backend-1/2 scheduling into one strict object [CONFIRMED]

## No.1 Move backend-1/2 scheduling into one strict object

### Code Change

Create `freestanding_gc_incremental_concurrent_scheduler.py` with raw pacing,
pause, tracing-step and allocation-notification exports.  Replace managed
helpers with exact extern aliases and make the backend-1/2 `pcc_gc_step` branch
delegate to the strict scheduler.  Preserve the denied auto-sweep boundary:
automatic allocation steps may finish marking and clear debt, but must not
sweep candidates.

### CONFIRMED

The new strict object uniquely owns all eleven scheduling symbols in source and
in the production archive.  LLVM and self objects have the same exact raw
closure.  Backend 1/2 public behavior, pcc1 threaded GC, five-backend
lifetime/finalizer/resurrection/weakref, referent-update, abstraction and
backend-4 production gates are green.  A fresh no-libpython/self pcc1 compiled
the real strict scheduler with eleven definitions, no `py_cpy_*` calls, and
clang accepted its IR.

## Report

No.1 landed without moving backend-3 promotion or backend-4 relocation policy.
The strict auto-step path contains no sweep call, preserving the prior denied
proposal boundary.  Evidence:
`docs/goal/evidence/2026-08-03-freestanding-gc-incremental-concurrent-scheduler.md`.
