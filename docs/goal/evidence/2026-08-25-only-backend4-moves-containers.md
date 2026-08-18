# Only backend 4 moves containers — the probe obligation was already complete

## What I set out to do, and why it was the wrong target

The row's open boundary, which I wrote myself an hour earlier, said the rooted
callback-restart contract was "proven against relocation but NOT under backend
3 generational forwarding plus eager slot rewrite".  That rested on an
assumption I had not checked: that backend 3 moves containers.

So I wrote a backend-3 probe — a dict insert whose user `__hash__` calls
`pcc_gc_generational_promote_scheduler_roots`, which reaches the registered
dict root and should oldify-copy it, leaving `py_dict_set`'s cached
`PyDictObject *` forwarding-stale.

It reported that the dict never moved:

```text
dict was not moved by promotion: 0x100abb420 == 0x100abb420
  known=1 flags=0x4008a young=1 pinned=0
```

The probe was written to return its own code (20) rather than 0 in that case,
which is the only reason this is visible.  Had it asserted just "the insert is
correct", it would have passed while exercising nothing — the smoke-input
failure mode.

## Why it does not move

`pcc_gc_generational_oldify_copy` gates on `pcc_gc_relocate_copy_supported_tag`:

```text
generational  INT FLOAT STR COMPLEX BYTES BYTEARRAY CPY_HANDLE
colored       + LIST TUPLE DICT SET + 17 more, then falls through to the above
```

Containers are absent from the generational set, so promotion declines them
outright.  Backends 0, 1 and 2 do not relocate at all.  **Backend 4 is the
only collector that can move a dict or set**, so the COLORED_RELOCATING-only
probe coverage is not a gap — it is exactly the reachable surface.

The three preconditions I would have suspected were all satisfied
(`known=1 young=1 pinned=0`); reading the tag gate is what actually answered
it.  Instrumenting the probe to report the conditions oldify gates on cost one
compile and replaced a plausible-but-wrong hypothesis about write barriers.

## What replaced the probe

A source-contract test,
`test_generational_promotion_declines_containers_so_backend4_probes_suffice`,
that asserts DICT/SET/LIST/TUPLE are **absent** from the generational tag gate
and **present** in the colored one, and that the strict mirror
(`freestanding_gc_generational_oldification.py`) gates on the identical set.

Its value is in the failure message: the day a container tag is added to the
generational list, the test fails and states that a
`PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR` probe is now required and that the
existing dict/set probes will not cover it.  A guard that names its own
obligation beats a boundary note that decays into a wrong assumption — which
is exactly what happened to the boundary text this replaces.

The mirror half matters on its own: if the strict gate ever admitted a
container the C gate refuses, the pcc-Python runtime would move an object the C
runtime will not, which is the mirror-drift class this repo keeps
rediscovering.  Both currently list the same seven tags.

## Gate

```text
-k generational_promotion_declines    1 passed in 0.41s
```

## What remains genuinely unproven

Movement is now fully bounded, but one class is still untested and is *not* the
one my old boundary named: under backend 1 the incremental write barrier can
advance the tricolor mark **while a container is half-published**, so a black
container holding an unmarked new entry could be freed early.  That needs no
relocation and no forwarding, so none of the backend-4 probes touch it.  Under
backend 2 the concurrent worker raises the same question off-thread.

## Nonclaims

- No probe exists for a callback that advances an incremental or concurrent
  mark mid-mutation.
- No bootstrap, stage or fixed-point gate was run.
