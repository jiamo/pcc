# Investigation: Backend #3 minor refill promotion

## Status
resolved

## Problem Description
Backend #3 now has a C-runtime minor bump arena, but arena refill still only
resets byte accounting. The next `goal.md` requirement is minor collection
behavior: when the minor heap fills, Backend #3 must run a promotion pass over
young objects and remembered old-to-young edges instead of leaving survivor
objects in the young generation indefinitely.

## Repro
```
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_promotes_remembered_young_child -q -n0
```

Expected current failure before the fix: after a minor arena refill, the
remembered old owner remains remembered and its young child remains young.

## Test [CONFIRMED]
`tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_promotes_remembered_young_child`

Observed with:

```
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_promotes_remembered_young_child -q -n0
```

Current result: fails with stdout `['1', '1', '1', '0', '1', '1']` instead of
`['1', '1', '1', '1', '0', '1']`. This confirms that storing a young child
through an old list marks the owner as remembered, but minor arena refill does
not promote the child or clear the remembered owner flag.

## Proposals
- No.1 Run bounded Backend #3 promotion work at minor refill     [CONFIRMED]

## No.1 Run bounded Backend #3 promotion work at minor refill
### Code Change
Teach the minor-heap refill path to invoke the existing Backend #3 promotion
step before resetting minor byte accounting. This should promote young
survivors, scan remembered old objects, and clear remembered flags in the same
single-domain pass already used by explicit `pcc_gc_step()`.
### CONFIRMED
The implementation extracts Backend #3's young-object promotion and
remembered-set scan into a helper used by both explicit `pcc_gc_step()` and
minor arena refill. The refill path now increments the minor collection counter,
runs bounded promotion work before resetting minor byte accounting, and leaves
the freshly allocated post-refill object young.

Confirmed with:

```
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_promotes_remembered_young_child -q -n0
```

Observed result: `1 passed in 3.31s`.

## Report (only when the investigation is closing)
No.1 landed. This is still a conservative promotion pass, not full OCaml-style
copying oldification with pointer rewriting. It does close the specific gap
from this investigation: a minor arena refill now performs real generational
work over young survivors and remembered old-to-young edges.
