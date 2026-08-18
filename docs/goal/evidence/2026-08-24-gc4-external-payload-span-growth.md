# GC4 external payload-span growth — 2026-08-24

## Claim

Backend 4 payload-span metadata can now describe a raw container payload that
outgrows the owning zpage.  Such a span retains its exact base, byte size,
owner provenance, store-buffer mapping and remembered-slot mapping, but records
`offset_bytes == -1` and is excluded from the zpage's internal
`used_bytes`/`allocated_bytes` accounting.

Registration may create either an internal or external span.  Growth may
transition an internal span to external, and later retargets keep updating an
external span.  Removal and relocation detach skip page-accounting changes for
external spans in both C and strict pcc-Python mirrors.

This is a correctness fix for ordinary large containers, not a stage2 speed
claim and not evidence for fixed point or broad five-GC parity.

## Failure and control

The focused Backend-4 list probe failed deterministically at append index 256:
capacity growth from 256 to 512 items requires a 4096-byte raw array, which
cannot fit in the remainder of the owner's zpage.  Span retargeting returned
failure and surfaced `MemoryError: list append: out of memory`.

This same failure reproduced with the contemporaneous `py_obj_sorted` pin
candidate removed, and an LLDB breakpoint on `py_obj_sorted` was never reached.
It therefore overturned the initial attribution to mutable-list pinning.  With
external spans implemented, the candidate-off 500-item sorted merge passed.

## Gates

- C and strict pcc-Python 500-item list growth: `2 passed in 166.26s`.
- Backend-3/4 candidate-off 500-item sorted merge control: `1 passed in 36.00s`.
- source/ABI/GC abstraction neighbors: `32 passed in 11.17s`.
- task relocation payload/forwarding retirement gate:
  `24 passed in 146.54s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-list-large-capacity-growth.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
4daf55aab6d59d7938e9dcab0c016a35129d52e7b8e70856963ffff9de506248  pcc/py_runtime/src/py_gc_backend.c
77d82699a7751487f189de534badd5eacedc8e391edb31c68f8851cafb611d24  pcc/py_runtime/py/py_gc_backend.py
223f4f19fbd14f5e22a326ac9e2188e7214d862214a1479809342893e7b19ffe  pcc/py_runtime/py/freestanding_gc_zpage_lifecycle.py
c52252a3f8f1a1faf7f01abc043e74d471dcd55afaa94347d9b1d38a4d85b7a3  tests/python/test_gc_threading_substrate.py
c0d2b902b84ff65278f8d3ec47a9324a8e73378d0f88a0391d06981295910a83  build/gc4-list-large-capacity-growth.log
d25cb677ea03591c54072cf91133bfa4164fc3b1e2938b242d590ee5a8ad55fb  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for the external payload-span growth slice.  The GC4 parent task
remains `IN_PROGRESS`.
