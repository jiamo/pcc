# Investigation: freestanding GC object-root seeding ownership

## Status

resolved

## Problem Description

`py_gc_backend.py` still directly walks the production object list to reset
mark colors and to gray pinned objects.  It then calls the already-strict
registered-root scanner.  These two raw object-list loops have no referent
semantics and can move independently of refcount-external-root subtraction,
which remains coupled to the later referent-traversal slice.

Generational promotion is not part of this slice.  It couples oldification,
backend-3 young lists, backend-4 zpages, owned/borrowed reference transfer and
recursive referent traversal and therefore is not a finite next step.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_object_root_seeding.py
```

Expected pre-change result: the strict source is absent and the ownership test
fails before any production archive probe runs.

## Test [N/A]

This is an ownership migration.  Closure requires exact LLVM/self object
imports, unique production archive ownership, direct color/FRESH_ALLOC/pinned
and registered-root behavior, existing GC0..4 root and referent differentials,
and fresh-pcc1 compilation.

## Proposals

- No.1 Move object-list mark preparation and current-root gray scan [accepted]

## No.1 Move object-list mark preparation and current-root gray scan

### Code Change

Create a strict freestanding object that owns two raw ABIs: one resets the gray
counter and initializes active object colors for normal or explicit collection;
the other grays pinned object-list entries and delegates frame, continuation,
scheduler and builtin-exception roots to the existing registered-root scanner.
Keep refcount-external-root calculation in the managed collector until the
referent visitor moves, preserving the existing ordering.

### Result

Accepted.  The strict object uniquely owns both loops with a two-definition,
four-import raw closure.  Production archive behavior, 163 GC downstream
checks and fresh-pcc1 compilation are green.  Refcount-external-root
subtraction remains intentionally coupled to the future referent visitor.
Evidence:
`docs/goal/evidence/2026-08-03-freestanding-gc-object-root-seeding.md`.
