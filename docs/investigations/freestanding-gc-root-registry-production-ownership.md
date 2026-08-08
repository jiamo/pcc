# Investigation: freestanding GC root registry production ownership

## Status
resolved

## Problem Description
`LIBC-P2-FREESTANDING-GC` requires the production scheduler and suspended
continuation root registries to be owned by strict freestanding pcc-Python.
Their read-only introspection was already split into
`freestanding_gc_root_introspection.py`, but registration and removal still
come from the large managed `py_gc_backend.py` object.  Trace and rewrite also
remain there and share the collector's mapped-root visitor and relocation
resolver; those rules must be moved as one later slice instead of duplicated
inside a registry-only module.

Predecessors:

- `freestanding-gc-root-introspection-ownership.md`
- `gc-scheduler-root-registry-thread-safety.md`
- `gc-scheduler-root-slot-registry.md`
- `gc-coroutine-scheduler-roots-production.md`

## Repro
Run the ownership gate before the new strict module exists:

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_root_registry.py
```

Expected pre-change result: the gate fails because
`freestanding_gc_root_registry.py` is absent and the six public registry
symbols are still exported by `py_gc_backend.py`.

## Test [N/A]
This is a production ownership migration rather than a newly reported runtime
failure.  The new gate must prove exact LLVM/self object closure, unique
archive ownership, deterministic C-oracle parity for GC0..4, and threaded
register/unregister contention before this investigation can close.

## Proposals
- No.1 Split scheduler and continuation registry mutation into one strict module [CONFIRMED]

## No.1 Split scheduler and continuation registry mutation into one strict module
### Code Change
Create `freestanding_gc_root_registry.py` as the sole production owner of the
scheduler handle/legacy APIs, continuation register/unregister APIs, their
locked list helpers, canonical root-map count/borrowed decoding, and the
release-store that requests a collection cycle.  Leave mapped-root
trace/rewrite in `py_gc_backend.py` until their shared visitor and relocation
resolver can move without duplicating object-graph rules.

### CONFIRMED
The six public registry mutation APIs and five shared helpers now have one
strict source owner. LLVM/self object closure, production archive ownership,
GC0..4 C-oracle parity, and threaded mutation/observation all passed:

```text
5 passed in 2.68s
16 passed in 3.65s
3 passed in 0.37s
32 passed in 5.78s
```

The current-source self/no-libpython stage1 completed in 43.937 seconds with
325 self-object cache hits and zero misses. That pcc1 compiled the real strict
module in 0.90 seconds; clang and `nm` confirmed its exact eleven definitions
and nine raw imports.

## Report (only when the investigation is closing)
No.1 landed. Registry mutation and root-map metadata are production-owned by
`freestanding_gc_root_registry.o`, and collection-cycle publication now uses
the C oracle's release ordering. Mapped-root trace/rewrite deliberately remains
in the collector object; its visitor and relocation resolver are the next
single-contract migration boundary.
