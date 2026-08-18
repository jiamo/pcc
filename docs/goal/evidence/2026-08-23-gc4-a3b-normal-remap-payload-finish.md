# GC4 A3b normal-remap payload finish

Date: 2026-08-23

## Claim

In normal Backend 4 two-epoch remap retirement, the C transition oracle and
strict freestanding pcc-Python production runtime now detach each forwarding
source's owned slots, independent raw payload storage and source side-table
metadata under the GC graph lock, but defer raw-storage frees, side-table token
release, saved-owned-slot decrefs and retirement-plan frees until the outer
object-drain, page-drain or idle-step caller releases that lock.

The caller-stack remap-finish plan is now 40 bytes. Its retained-page,
forwarding-node, identity-node, object-node and payload-plan chains occupy
offsets 0/8/16/24/32. The payload finish runs before forwarding-target decref,
preserving the established ownership order while ensuring decref reentry sees
an inert, unindexed source and a non-resolving edge.

The target-death path remains deliberately distinct. Its public
`pcc_gc_relocation_retire_source_payload` ABI composes the same payload detach
and finish immediately under the existing object-freeing graph lock, so this
slice does not claim the historical OWNED self-reference ordering is safe for
post-unlock target-death finish.

## Pre-lock preparation audit

The task-board request to move payload preparation before the graph lock was
audited and denied as an immediate source change. The three normal-remap
callers do not yet own a stopped-world epoch before locking. The payload slot
visitor heals raw source slots, while
`pcc_gc_backend4_source_side_table_plan_prepare` directly traverses the
graph-lock-owned medium-state and heap store-buffer lists. Moving those reads
outside the lock would race mutators and table mutation; snapshot/unlock/
prepare/relock additionally needs a proven source/edge pin and ABA exclusion.
Two-epoch source-page retention is not that proof.

This slice therefore isolates locked commit from reentrant finish without
overclaiming pre-lock allocation/validation. Pre-lock preparation remains
blocked on the parent stopped-world/raw-access/source-lifetime contract.

## Frozen source identity

```text
5cbbbe1b35b97e230a2e5f9cf17ab342feda2fb47050b9015761e3fb4c364076  pcc/py_runtime/src/py_gc_backend.c
805d80231a5949f54f4d3b5eb6659b7acdbd55ed9b7f5d3b7f789f71aec6acf6  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
ff708de1f50021caeac40c3e1ce5ad29300b43cd29cafb8ea49240da6a0ba9e3  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
f5f6e1c05f2c9df1b0ff116eb7e92c04d9b6e92498cfd350a7c6b57944d09050  pcc/py_runtime/py/freestanding_gc_relocation_drain.py
040ae5c61a8ad8f6679f60c013e7e69cc596d03cca4cac7376edf7a1aa31c9fa  pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py
67c89e19b48f00a51314d0f05e04cef34f9c106d61b8bee1414a482f4fe4284c  pcc/py_frontend/codegen/runtime_abi.py
ca3f06ce36622c2b5fcd31b7b60ed31460f16d488b87ef82a0225f3e133570ff  tests/python/test_freestanding_gc_relocation_payload.py
b248edf97c464143ca8dd38e8939af78a576ef10727bf51ed3221d7ee4ddbb24  tests/python/test_freestanding_gc_forwarding_retirement.py
```

These hashes remained unchanged through the final dynamic packets.

## RED and implementation

The source/ABI/order regression was genuinely RED on the former normal-remap
call to the immediate public payload-retirement ABI:

```text
1 failed in 0.12s
```

The two runtime roots now expose an internal
`pcc_gc_relocation_retire_source_payload_into_finish` transition. It performs
the same two-pass slot validation and side-table snapshot before mutation,
then NULLs owned slots, detaches raw payloads and commits side-table/zpage
removal under the lock. Instead of releasing anything reentrant, it chains a
fully detached plan into the shared finish object. One
`pcc_gc_relocation_finish_source_payloads` owner frees raw bases, releases
side-table tokens, decrefs saved source-slot ownership and frees plan storage
after unlock. The generic remap finish clears all five chains before invoking
owners and consumes payload plans before forwarding nodes.

Strict closure validation caught that the finite extern scanner did not admit
a three-line two-argument declaration. Reformatting it into the repository's
canonical module-scope extern shape made the exact LLVM/self closure green;
no fallback or ABI widening was added.

## Focused gates

The task-card full payload/forwarding gate, including LLVM/self closures,
production-archive ownership and C/strict three-remap differential behavior:

```text
18 passed in 132.62s
```

Log: `build/gc4-relocation-mutator-quiescence.log`, SHA-256
`bd1f968c47a3847cfd877007a7528079ee42c62412886b98969a6066df95214e`.

All fourteen C/strict type-specific raw-payload cases:

```text
14 passed in 134.62s
```

Log: `build/gc4-a3b-normal-remap-payload-finish-raw.log`, SHA-256
`f65308da4018eaf6012c9d81010eb8bc19d2f7676450b094d78995a2f1faafc7`.

Fragmentation, stable ID, C/strict target phase reset and GC3 oldification
compatibility:

```text
9 passed in 23.79s
```

Log: `build/gc4-a3b-normal-remap-payload-finish-compatibility.log`, SHA-256
`d9eebf3ad8a14ffe1e7511c742a74ae2aa951df682b0faeb6d9b026de872ba16`.

Fifteen non-archive source/ABI/order/closure nodes passed before the complete
gate. Python byte-compilation passed for all affected strict/ABI/test files. C
syntax passed with `PCC_WITH_THREADS=0` and `=1`; both reported only the same
five pre-existing unused-static-helper warnings. `git diff --check` was clean.

One separately requested adjacent static node,
`test_backend4_relocation_reuses_shared_slot_contract`, is baseline-red on
`HEAD`: both current and `HEAD` C use the entry-based
`PccGcRelocateSlotPairs`, while the unchanged test still splits on the removed
`PyObject ***from_slots` typedef. It failed before reaching a semantic
assertion (`IndexError`, `1 failed in 0.09s`). The failure predates this slice,
is outside its payload-finish contract, and was retained rather than silently
rewritten or counted as green.

## Open boundary

Payload record/context/side-table allocation and validation still occur under
the normal-remap graph lock. Moving them out requires stopped-world ownership,
raw-access quiescence, a stable source/forwarding-edge lifetime and ABA-safe
revalidation. Structural side-table/index loops likewise remain lock-held.

Target-death payload cleanup remains immediate and graph-locked. Before it can
share delayed finish, a dedicated RED must prove an OWNED source
self-reference is made non-resolving before any saved token can decref the
already dying target. The baseline-red stale-candidate/fairness case remains
unchanged. Mutator admission, remembered-root admission, allocator failure,
lock-free readers, nested/concurrent drains, backend switching, callbacks/raw
leases, resurrection, physical movement, A3c, broad parity, performance and
fixed point remain open. No bootstrap chain, broad default suite, performance
gate or five-GC matrix was run. The parent task remains `IN_PROGRESS`.
