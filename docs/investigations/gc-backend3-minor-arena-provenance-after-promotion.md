# GC3 minor-arena provenance after legal promotion

## Status

resolved (2026-07-18)

## Problem Description

The pcc-Python GC3 minor-arena wrapper reported `0` instead of `4096` for the
`PY_FLAG_GC_MINOR_ARENA` bit, then the outer environment wrapper was reported
as interrupted.  This is a successor audit to
`gc-backend3-pcc-py-minor-arena.md` rather than evidence that the original
minor allocator disappeared.

## Layered diagnosis

The exact inner node failed deterministically in 0.55 seconds; it was not
intrinsically hanging.  Its telemetry was `8 allocations, 1 refill, 8 bumps,
0 fallbacks`.

The runtime-high path was traced precisely:

```text
py_gc_backend.py -> Python frontend LLVM IR -> llvmlite ir_to_obj
test probe       -> backend=self
```

The generated LLVM IR and AArch64 object both returned the bump pointer and
stored the pending minor block correctly.  LLDB at the first 64-byte object's
allocation hook observed a non-null pending block and flags `0x1088`, which
contains `0x1000`.

A hardware watchpoint then observed later allocation pressure promote that
root through `generational_oldify_copy`.  The root was rewritten to the old
copy, whose minor-arena ownership bit is intentionally cleared.  Existing
promotion tests explicitly require forwarded old objects not to carry
`PY_FLAG_GC_MINOR_ARENA`.

## Fix

The provenance assertion now runs immediately after the first allocation,
before legal promotion.  The loop still checks all eight allocation events,
one refill, eight bumps, and zero fallbacks.  Dedicated promotion/root-rewrite
tests retain ownership of the later transition, so capability was not removed
or weakened.

## Confirmation

```text
inner GC3 node: 1 passed in 0.61s
frontend=llvm / GC3 outer wrapper: 1 passed in 0.98s
```

Both commands completed under bounded timeouts with no surviving compiler or
pytest children.  The earlier `KeyboardInterrupt` was an outer-run
interruption layered on top of a fast stale assertion, not a two-hour GC3
operation.

## Report

GC3 minor allocation and its telemetry were correct.  The test read an
allocation-origin bit after the object had legitimately changed generations.
The test now distinguishes allocation provenance from promotion state.

