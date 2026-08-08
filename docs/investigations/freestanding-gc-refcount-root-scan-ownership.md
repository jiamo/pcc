# Investigation: freestanding GC refcount external-root scan ownership

## Status

resolved

## Problem Description

After object-list mark preparation moved, `py_gc_backend.py` still owns the
three-pass refcount external-root scan: snapshot object refcounts into node
`gc_refs`, subtract internal referent edges, then gray nodes with positive
external references.  The list traversal and node bookkeeping are raw GC
kernel work; type-specific referent enumeration remains semantic provider work.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_refcount_roots.py
```

Expected pre-change result: the strict source is absent.

## Test [N/A]

Ownership closure requires exact LLVM/self imports, unique archive owners and a
list parent/child probe that distinguishes an internal-only child reference
from a separately retained external child reference.

## Proposals

- No.1 Move the three-pass object-list scan behind one strict ABI [accepted]

## No.1 Move the three-pass object-list scan behind one strict ABI

### Code Change

The strict object owns object-node activity checks, refcount snapshots and
external-root gray decisions.  It calls one explicitly named managed provider
for referent subtraction.  This preserves one slot-graph rule and leaves the
provider eligible to move together with the full referent visitor.

### Result

Accepted.  The strict object owns the activity predicate and three-pass scan;
the one managed referent provider remains explicit.  Exact object closure,
production archive ownership, direct internal/external reference semantics,
149 downstream tests and fresh-pcc1 compilation are green.  Evidence:
`docs/goal/evidence/2026-08-03-freestanding-gc-refcount-root-scan.md`.
