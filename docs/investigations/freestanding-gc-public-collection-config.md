# Investigation: freestanding GC public collection and config

## Status

resolved

## Problem Description

The strict collector now owns the complete common GC1..4 mark/sweep kernel,
but four public tracing entrypoints remain in `py_gc_backend.py`.  Moving only
those wrappers would leave a reverse dependency on managed `_init_config`, so
the entry boundary would not actually be freestanding.  `_init_config` has one
finite extra dependency: the state-only CMS worker-start accounting helper.

This slice therefore moves environment parsing, config initialization, the
CMS start accounting primitive and exactly four public tracing entrypoints.
Auto-step, CMS assist/drain/stop, generational and relocating policy remain
outside the slice.

Predecessor:
`docs/investigations/freestanding-gc-tracing-sweep-collector.md`.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_public_collection.py
```

Expected pre-change result: `freestanding_gc_public_collection.py` is absent;
the managed backend still defines `_parse_env_i32`, `_init_config`,
`_maybe_start_cms_worker` and all four public entrypoints.

## Test [CONFIRMED]

The focused pre-change gate fails because the strict source is absent.  The
completed gate will require one source/archive owner, exact LLVM/self closure,
the existing environment defaults/clamps, CMS-start accounting, backend
admission, stop-the-world sweep ordering and explicit-collection state order.

## Proposals

- No.1 Move config and public tracing collection into one strict object [CONFIRMED]

## No.1 Move config and public tracing collection into one strict object

### Code Change

Create `freestanding_gc_public_collection.py` with raw config parser/ensure,
CMS-start accounting and the four public entrypoints.  Replace managed config
and CMS-start definitions with exact extern aliases and remove the four public
definitions.  Do not change environment names, defaults, clamps or collector
scheduling.

### CONFIRMED

The new strict object uniquely owns all seven config/public-collection symbols
in source and in the production archive.  LLVM and self emission have the same
exact raw undefined closure.  Focused config, incremental/concurrent,
five-backend lifetime/finalizer/resurrection/weakref, referent-update and
backend-4 production gates are green.  A fresh no-libpython/self pcc1 compiled
the real strict module with seven definitions, no `py_cpy_*` calls, and clang
accepted its IR.

## Report

No.1 landed without moving auto-step, CMS assist/drain/stop, generational or
relocating policy.  Evidence:
`docs/goal/evidence/2026-08-03-freestanding-gc-public-collection-config.md`.
