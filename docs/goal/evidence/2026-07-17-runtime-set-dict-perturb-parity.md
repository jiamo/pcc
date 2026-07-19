# Set/dict negative-hash probe parity — 2026-07-17

Task: `AUD-P1-RUNTIME-MIRROR-PARITY`.

## Claim

The C and pcc-Python set/dict mirrors now use the same unsigned 64-bit
perturbation sequence for negative hashes and the same `capacity * 2` bounded
probe policy.  This closes the selected runtime-mirror family; it does not
claim broader runtime or five-GC equality.

## Changes and causality

- C already treated hash perturbation as `uint64_t`; the pcc-Python ports
  instead cleared the sign bit, producing a different sequence for negative
  hashes.  Both ports now retain the complete hash bits and use a shared-shape
  `_perturb_shift5` helper.  For a negative signed-i64 input, arithmetic
  right-shift plus `2**59` is exactly the corresponding unsigned right-shift.
- The pcc-Python set's port-only `probes > capacity` escape differed from both
  dict mirrors, while C set had no bound.  Both set mirrors now use the dict
  policy: inspect at most `capacity * 2` slots, then choose the first tombstone
  or slot zero as the defensive miss target.
- The parity guard requires identical pcc-Python helper bodies, the unsigned C
  perturb declarations/shifts, equal probe bounds, and compiled IR containing
  the logical-shift correction rather than the old sign-bit mask.
- The negative-string-hash self-backend workload proves the pcc-Python set
  sequence terminates and retains membership behavior.
- The previously suspected backend0 frame-root latch gap was audit-stale: C
  and pcc-Python both initialize the latch to zero, set it when backend 0 is
  explicitly selected, and consult it in the same two frame-root decisions.
  The source parity guard now preserves that invariant.
- The finalizer-cache item was closed separately by
  `AUD-P1-RUNTIME-CLASS-LAYOUT-MIRROR`.

## Gates

- Source/IR parity plus negative-hash execution:
  `4 passed in 6.76s`.
- Final required runtime gate:
  `55 passed in 39.06s`.
- Task-board validation: `OK: 101 tasks validated` before final promotion.

No bootstrap, five-GC matrix, or GCC suite was run for this finite mirror
contract.
