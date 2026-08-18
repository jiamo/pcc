# GC4 generic object-slot store transaction — 2026-08-24

## Claim

For the generic `pcc_gc_store_ptr` path, backends 1-4 now perform forwarding
canonicalization, prepared NEW retain, owner-aware write barrier, OLD slot
load, raw publication, and prepared OLD release in one graph-lock/no-park
tenure.  Potential logging, diagnostic, finalizer, weakref, free, and recursive
GC tails run only after the outer graph unlock.  Backend 0 retains its direct
refcount fast path.  The C runtime and strict pcc-Python runtime share the same
128-byte plan ABI and ordering.

This is a finite sub-claim of `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`, not
completion of that task.  Dict/set rehash and raw-base replacement, source/page
lifetime across unlocked preparation, constructor publication, C-API leases,
callback roots, resurrection, and stale-candidate fairness remain open.  The
three-party pause proof will be attached to the real container transaction,
where the collector request overlaps an actual high-level list/dict/set access;
this evidence does not substitute the helper-only active-cycle probe for that
gate.

## Implementation

- `pcc_gc_store_ptr` initializes/logs before graph acquisition, invokes the
  owner-aware locked plan, unlocks, then finishes prepared refcount tails.
- The shared plan canonicalizes backend 3/4 forwarded NEW values, retains NEW
  before the owner-aware barrier and publication, then prepares OLD release.
- Store-pointer finish suppresses the root-store-only NULL-owner log, avoiding
  duplicate historical telemetry.
- Internal C declarations and strict cross-object signatures expose only the
  commit/finish seam needed by the split runtime objects; they remain absent
  from the public managed runtime ABI.
- Signature dictionaries were mechanically re-chunked to the existing
  at-most-50-entry self-host contract after accumulated GC additions exposed
  oversized chunks; key order and assembled dictionaries are tested.
- `pcc_gc_store_ptr_fresh_native_instance` remains isolated to
  `py_list_append_fresh_native_instance`; no generic movable dict/set route
  consumes it.  Its constructor-publication proof remains open.

## Correctness diagnosis retained

The first five-GC abstraction run expected a backend-1 child to become gray,
but LLDB at the current binary proved `backend_selected == 1`, black owner and
white child were passed correctly while `mark_active == 0` before
`pcc_gc_store_ptr`.  Existing barrier semantics deliberately do not fabricate a
tricolor cycle outside an active mark epoch.  The stale timing-dependent
expectation was corrected to white, and a new deterministic C/strict probe
starts a real cycle, retains 63 gray work items, then proves a black-owner to
white-child generic store shades the child.  Thus the denied hypothesis was
"the new transaction lost the barrier"; the confirmed behavior is
cycle-labeled shading.

## Gates

- C syntax, `PCC_WITH_THREADS=0` and `=1`: pass.
- strict `py_obj.py` no-libpython/self-backend single-module closure: pass.
- ABI chunking plus root/store source contracts: `4 passed in 0.12s`.
- scheduler root-pop finalizer re-entry, backend 3/4 x C/strict:
  `4 passed in 140.91s`.
- real container reference-balance/UAF regressions: `8 passed in 8.81s`.
- runtime/generator write-barrier routing: `8 passed in 24.57s`.
- real active incremental cycle, C/strict: `2 passed in 1.23s`.
- five-GC abstraction surface: `15 passed in 10.76s`.
- relocation payload plus forwarding retirement task gate:
  `24 passed in 144.90s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc-store-root-pop-finalizer.log`
- `build/gc-store-ptr-balance.log`
- `build/gc-store-ptr-write-barrier.log`
- `build/gc-store-ptr-active-cycle.log`
- `build/gc-store-ptr-abstraction-surface.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
fa5ecb0f635a0138de49ec49ce9284d30edddb986bc07553b577f5bf080a98e4  pcc/py_runtime/src/py_obj.c
1cf5ca3c26a60ab14931725dce7a8227e4d933813385980c92cb3b44f6c3b4d2  pcc/py_runtime/py/py_obj.py
0a198e6f6940e2849512d82835b9494d14f9ed1e0ae8982fdcdefe80fe4fa56f  pcc/py_runtime/src/py_internal.h
d0b39c175162d8a66ef18be367ea47ce8005caf72bfe9ba9a8693358a4838383  pcc/py_frontend/codegen/runtime_abi.py
c9f765274f6bba30f0d9530e6ae32bf4e047ff1795c1c9181d8876fe4b47b804  tests/python/test_gc_threading_substrate.py
d8e054a5d79fa64fb49e6aee9723526be0f57256bc766dc2debfac719d941321  tests/python/test_gc_abstraction_surface.py
656e4e65ccc8b8980927e0fcd31150f148122bc27c53fe4ec49fa3eb1f0c1771  build/gc-store-root-pop-finalizer.log
81f9dfe0d81cd734f36458ba3a7d5fc88072ac9561e952763b412836b41f6f03  build/gc-store-ptr-balance.log
8d23d6d5f6b4bedc1166753edec87b79fc261452e5cd6ff93fac8cef9aacb56c  build/gc-store-ptr-write-barrier.log
a569895cdabc1f1b06db9bf420d98c6058c9b9e14b709c665ab8dd0b937c81c1  build/gc-store-ptr-active-cycle.log
731f54cf0b14add39fe0c44ca9fe83e7379ca7fa005745b0c85c34c4ec09fd34  build/gc-store-ptr-abstraction-surface.log
495f3fafeb925cb65f71989b3d4ad34cb8534065ed80cfd77ed510c638363916  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for the generic pointer-slot helper sub-claim only.  Parent task
remains `IN_PROGRESS`; next boundary is dict/set rehash and raw-base replacement
under one owner-canonical, source-live graph/no-park transaction, followed by
the required real-container three-party pthread proof.
