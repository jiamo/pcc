# Investigation: Freestanding GC object-node and young-list substrate

## Status

resolved

## Problem Description

Backend 3 copy-oldification cannot become an independent strict module while
allocation tracking still depends on the managed object-node pool, doubly
linked object list, known-size lookup, live-byte subtraction, and intrusive
young-object worklist in `py_gc_backend.py`.

This slice moves that raw pointer bookkeeping as one finite substrate.  It
does not move per-type payload copying, promotion slot rewriting, remembered
owners, or the generational step dispatcher.

## Repro

```bash
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_object_nodes.py
```

Before the slice, the strict source owner does not exist.

## Test [CONFIRMED]

The initial owner test failed in 0.09 seconds with `FileNotFoundError`, proving
the strict owner was absent.

## Proposals

- No.1 Move object-node/list/young-worklist primitives to strict pcc-Python [CONFIRMED]

## No.1 Move object-node/list/young-worklist primitives to strict pcc-Python

### Claim boundary

- one strict source owns node layout access, bounded node pooling, object-list
  unlink, Backend 3 young link/unlink/rebuild, known-size lookup, and
  saturating live-byte subtraction;
- managed GC policy consumes the moved raw operations only through explicit
  cross-object ABI declarations;
- LLVM/self exact closure and production archive unique ownership are green.

### Explicit non-claims

- no promotion, oldification payload copy, remembered-set drain, or Backend 4
  relocation policy moves in this slice;
- no full-GC or fixed-point completion claim is made.

### CONFIRMED

- strict source/closure/archive gate: 5 passed in 64.32 seconds;
- three targeted GC3 young-list/oldification checks passed in 60.89 seconds;
- fresh no-libpython self-backed pcc1 completed in 81.383 seconds and compiled
  the real strict module with 30 definitions and no `py_cpy_*` calls.

## Report

Raw object-node/list/young-worklist ownership moved to one strict
freestanding pcc-Python module.  The remaining Backend 3 work is now policy:
per-type copy-oldification plus root/slot promotion and the step dispatcher.
