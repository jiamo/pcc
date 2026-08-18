# Investigation: Backend #4 forwarded sources retain copied payload ownership

## Status

resolved

## Problem Description

Backend #4 relocation gives the target an independent ownership share for every
`PCC_GC_SLOT_OWNED` referent and, for several object kinds, a distinct raw
payload allocation.  The later forwarded-source retirement transaction removes
the source from identity, object, exact-provenance, and forwarding indexes, but
does not release the source's owned slots or raw payload.  The target therefore
keeps the copied ownership while the retired source's ownership remains leaked.

This is a successor to the already-resolved
[`freestanding-gc-relocation-payload.md`](freestanding-gc-relocation-payload.md),
[`freestanding-gc-forwarding-retirement.md`](freestanding-gc-forwarding-retirement.md),
and
[`gc-backend4-relocation-shared-slot-contract.md`](gc-backend4-relocation-shared-slot-contract.md)
investigations.  Those documents correctly established copy ownership,
one-epoch retirement, and the shared slot visitor; they must not be rewritten to
absorb this newly observed cross-transaction lifetime defect.  Backend #3's
separate minor-source lifecycle remains documented in
[`gc-backend3-forwarded-minor-source-cleanup.md`](gc-backend3-forwarded-minor-source-cleanup.md).

## Repro

Run the single downstream GC4 ordinary-slab fallback node with fail-fast,
durable output:

```bash
gtimeout 450s zsh -o pipefail -c 'gtimeout 420s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_granule_map.py::test_granule_gc4_downstream_fallback_tail_retires_object_slab_source 2>&1 | tee build/granule-s2-gc4-payload-retirement-red-v2.log'
```

The compiled probe must return zero and report the child-reference sequence
`2 -> 3 -> 2 -> 1`: source plus observer, relocated-target copy ownership,
source-payload retirement, and final root cleanup.  The confirmed current
behavior instead returns 14 and reports `2 -> 3 -> 3 -> 2` with `counts 0`.

## Test [CONFIRMED]

The command in `## Repro` was observed red on 2026-08-22:

```text
tests/python/test_gc_granule_map.py::
  test_granule_gc4_downstream_fallback_tail_retires_object_slab_source FAILED
compiled probe rc=14
child 2 3 3 2 counts 0
1 failed in 275.46s
```

The durable log SHA-256 is
`f7aaebb7227bc34035765a0d5ddc09b23360f181191dbb1a1cdee165dedd8ae6`.
The test SHA-256 is
`f5bc414ad9d5da161c24decc2c2d29789680e8bdc7bec3f6b4e75166ea0fc6bf`.
The runtime source identity exercised by that log is:

```text
baf99ebe6548b8a0bf434d3214786b23917c479795fd7e143dcd9b6eddf2a145  pcc/py_runtime/py/freestanding_allocator.py
d6ef106a5ac74a927e399714c06e49e40abc3e9535b39ebf04a7ecf63646cabd  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
a2c2b4e38aabc6ae89127735b87cda681cecb2e7dfd0b25c636161c300d4d3c9  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
a0af3c9b513566f1d5dfd4129973a95148991fcd1dd66230b57c22c591a97aa0  pcc/py_runtime/py/py_gc_backend.py
545b6cc52d8fc543dd6c2b69fcec73bdcd363f43ba190c8361004719736183ea  pcc/py_runtime/src/py_gc_backend.c
187223b21d57b1c87b3ca0f1c0b183c030a3ab0d288e01b128fbf03d12867767  pcc/py_runtime/include/py_runtime.h
```

This confirms one pcc-Python runtime archive / GC4 downstream-tail defect.  It
does not yet prove a C-oracle mismatch, an actual zpage allocation failure, all
payload kinds, the normal and target-dies-early retirement routes, or any
stage/bootstrap result.

## Adjacent source-owned metadata audit [CONFIRMED]

The same retirement boundary owns more than referent refcounts.  The public
`pcc_gc_install_forwarding` ABI can install a pointer-bearing forwarding edge
without first running relocation-copy commit.  On that path the source can
still own GC4 store-buffer entries, remembered-slot/card entries, a zpage owner
node, and raw-payload span accounting.  Freeing list/dict/set/class or other
source raw storage while those entries remain would leave side tables pointing
at released slot addresses.

The existing owner-wide store-buffer removal is also not safe to call as an
intermediate fix: both mirrors decrement queued values before every matching
entry across all buffers has become globally invisible.  A finalizer/decref
reentry can therefore observe and remove the same owner entry again.  The
repair must prepare all fallible storage first, make the source slots and raw
metadata inert, detach every owner store-buffer and remembered entry without
running decref, remove the full zpage owner/span/accounting bundle, and only
then free raw bases and release the detached ownership tokens.  This is a
source-audit finding; no dynamic green claim has been made for it yet.

The target-dies-early route has an additional self-reference boundary.  Its
reverse target index is detached first, but the main source-to-target edge is
still live while source slots are healed.  A source OWNED self slot therefore
heals to the target that is already in logical deallocation; releasing the
saved source ownership before the main edge is made non-resolving can decref a
zero/deallocating target or let cleanup reentry resolve the source back to that
target.  The target-dies transaction must prepare/heal while the edge is still
available, then make that edge non-resolving before any saved-value decref, and
prove the exact ownership disposition for self-target tokens.  Normal two-epoch
self-forwarding still requires an ordinary exactly-once release.

## Proposals

- No.1 Retire source payload ownership while forwarding is still live [pending]

## No.1 Retire source payload ownership while forwarding is still live

### Code Change

Add one finite strict ABI, provisionally
`pcc_gc_relocation_retire_source_payload`, owned next to relocation payload
copying and mirrored in C.  Before either retirement route clears the
`FORWARDED` state or unlinks the forwarding edge, it must:

1. walk the authoritative shared source-slot contract, heal each slot, and for
   `PCC_GC_SLOT_OWNED` only, null the source slot before releasing its referent;
2. free and null source-only raw storage for the supported continuation,
   exception, class, list, dict, and set payload shapes, then zero the related
   source metadata;
3. detach all source-owned store-buffer and remembered-set/card entries before
   any cleanup decref, remove any remaining zpage owner/payload-span accounting,
   and only then free the detached raw bases and release saved ownership; and
4. preserve transfer cases such as the already-nulled memoryview buffer and
   inline-only tuple payloads without adding a second object-graph rule.

Invoke the ABI from both normal two-epoch retirement and the target-dies-early
cleanup path.  Keep header/page recycling, finalizer dispatch, weakref unlink,
continuation-root registration, scheduler cleanup, and unrelated collector
policy with their existing owners.

### pending

Implementation and green evidence are not yet recorded.  The proposal is
accepted only when C and strict pcc-Python use one ABI/order contract, the
confirmed node reports `2 -> 3 -> 2 -> 1`, both retirement routes release
exactly once, direct-forwarding leaves no source store-buffer, remembered-card,
zpage-owner or payload-span metadata, and every supported raw/inline payload
kind is differential-equal without weakening the shared slot visitor.  The
target-dies route must additionally pass an OWNED self-reference case without
resolving or decrefing through an already deallocating target.

## Claim Boundary

This P0 blocks the granule S2 module98 A/B and every subsequent stage or
fixed-point run.  Fixing it will establish forwarded-source payload retirement
only; it will not by itself prove real zpage OOM fallback, physical recycling
of an ordinary-slab source header, five-GC acceptance, stage2 performance, or
the pcc1 -> pcc2 -> pcc3 fixed point.  Exact-index allocation rollback remains
owned by `ARCH-P1-EXACT-INDEX-ALLOC-ROLLBACK`.

## Update — 2026-08-25 implementation and focused gates confirmed

### CONFIRMED Proposal No.1

The C and strict implementation now exists under one ABI and is called from
normal two-epoch and target-dies paths before forwarding teardown. The original
confirmed node is green with required `2 -> 3 -> 2 -> 1`; the 24-node source/
closure/normal/target-death/C-strict differential is green. Exact evidence is
in `docs/goal/evidence/2026-08-25-gc4-forwarded-source-payload-retirement.md`.

A later set-remove control found strict dynamic C-extension key dealloc remains
zero. Source retirement does save/null and call `py_decref` on that OWNED set
slot; direct strict bit62 dealloc dispatch works. The object remains unmanaged/
unknown from allocation, matching `GC-P0-CEXT-STRICT-DECREF-TAG-PARITY`.
Route it there; do not reopen source-payload retirement or retry tag-only
decref exemptions.
