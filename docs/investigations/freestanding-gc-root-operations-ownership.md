# Investigation: freestanding GC root operations ownership

## Status
resolved

## Problem Description

The strict mapped-root visitor still calls two managed providers for root gray
and relocation resolution. `py_gc_backend.py` also owns the no-lock
known-object predicate used throughout the collector, and its pcc-Python gray
counter uses plain loads/stores while the C oracle uses acquire/release and
acq_rel operations.

Promotion is deliberately excluded: it includes generational oldification,
referent recursion and ownership transfer and needs its own larger slice.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_root_operations.py
```

Expected pre-change result: the strict module is absent and root gray,
resolution and known-object ownership remain in `py_gc_backend.py`.

The first fresh-stage compile exposed two stacked frontend failures.  They are
separate from the root-operation ownership change:

1. A mixin method's typed ``self.field += value`` used dynamic
   ``py_obj_getattr`` instead of the inferred concrete receiver field slot.
   In pcc1 this passed ``NULL`` to the in-place add in ``_fresh``.  The fix is
   covered by
   ``test_mixin_self_scalar_augassign_uses_receiver_field_layout``.
2. Once that was fixed, CAS lowering reached
   ``IRBuilder.extract_value(pair, 0)``.  The scaffold's opaque-pointer ABI
   represented the literal zero as ``NULL``; the compiled method then tried
   to iterate it and raised ``TypeError``.  Both llvmlite and the in-repo
   builder accept an explicit index sequence, so CAS now uses ``[0]``, matching
   existing aggregate extraction call sites.

## Test [N/A]

This is an ownership and synchronization migration. Closure requires exact
LLVM/self object closure, unique archive ownership, direct backend-4 gray and
relocation resolution behavior, a threaded atomic counter stress, existing
GC0..4 mapped-root C differentials, and collector downstream gates.

## Proposals

- No.1 Move root gray/resolve/known and gray-count atomics into one strict object [accepted]

## No.1 Move root gray/resolve/known and gray-count atomics into one strict object

### Code Change

Create `freestanding_gc_root_operations.py` as the sole owner of the no-lock
known-object predicate, root gray, root slot resolution and four gray-count
atomic operations. Managed helpers become thin calls to those raw ABIs. Keep
promotion and all referent traversal in the managed collector for now.

### Result

Accepted.  The strict object uniquely owns all seven symbols.  Exact
LLVM/self/fresh-pcc1 closure, production backend-4 semantics, threaded counter
stress and 154 downstream GC tests are green.  The two stacked frontend
failures described above have focused regressions and the corrected fresh
pcc1 compiles this module successfully.  Evidence:
`docs/goal/evidence/2026-08-03-freestanding-gc-root-operations.md`.
