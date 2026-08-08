# Freestanding GC semantic-closure evidence (2026-08-03)

## Claim boundary

The current production no-libpython/self runtime passes the task's focused
weakref, finalizer, resurrection, suspended-frame, scheduler-root,
C-extension-slot/root, relocation, and synchronization gates across the
applicable five-backend matrix. This is semantic evidence, not the final
pcc1/pcc2/pcc3 fixed-point or long-running performance proof.

## Five-backend and root gates

One combined run collected 203 tests: the complete 168-test
`gc_production_contract`, 31 C-extension/referent/root tests, and four
suspended-frame/scheduler-root C-versus-pcc-Python archive tests. It completed
with 202 passes and one stale source-owner assertion: the assertion still read
`py_gc_backend.py` after PROMOTE and UPDATE root visitors had migrated to the
strict generational scheduler and forwarding-retirement objects.

The assertion was routed to those real owners. Its focused rerun passed, and
the complete 31-test referent/root file then passed:

```text
tests/python/test_gc_update_referents.py
  31 passed in 0.47s

tests/python/gc_production_contract
  168 tests collected; all 168 passed in the combined run

C and pcc-Python suspended-frame/scheduler-root archive differentials
  4 passed in the combined run
```

The production contract compiles strict `--backend self
--python-libpython=off --ir-scaffold=on` executables and runs each under
`PCC_GC_BACKEND=0..4`. Its families include exception roots, value payload
roots, weakrefs, finalizers, resurrection, slot graphs, and lifetime policy.

## Relocation and synchronization gates

```text
pcc-Python colored-relocating task/list/tuple/set/dict/instance/target gates
  8 passed, 17 deselected in 0.62s

threaded frame-registry and mapped-root mutation C-oracle differentials,
each under GC0..4
  2 passed in 7.49s

strict C-extension slot callback delegation
  1 passed in 0.64s
```

These are archive-linked pcc-Python runtime checks, not host-only source
inspection. The exact production link ownership is recorded separately in
`2026-08-03-freestanding-gc-production-link-map.md`.

## Scoped hashes

```text
4d8b34a96f9d2e101f9a1853a3da85a2233a802f6f706f09e34afb9f7e80bea1  tests/python/test_gc_update_referents.py
e2658b63e0f43dcd0bf91177e22977e413301f739f51d414599d428aa72f7b43  tests/python/test_freestanding_gc_production_link_map.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Run the one-shot five-GC semantic/fixed-point bootstrap matrix, then record
long-running RSS, fragmentation, pause, and throughput deltas for GC0..4.
