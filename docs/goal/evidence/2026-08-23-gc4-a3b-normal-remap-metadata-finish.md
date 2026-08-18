# GC4 A3b normal-remap source-metadata finish

Date: 2026-08-23

## Claim

In normal Backend 4 two-epoch remap retirement, the C transition oracle and
strict freestanding pcc-Python runtime now detach stable-identity and object
nodes while holding the GC graph lock and physically free those detached nodes
only after the outer object-drain, page-drain or idle-step caller unlocks.

All semantic invalidation remains locked: identity, object and managed-pointer
indexes; granule/exact provenance; live-byte accounting; young/object-list
unlink. The caller-stack remap-finish plan is now 32 bytes with retained pages,
forwarding nodes, identity nodes and object nodes at offsets 0/8/16/24. Each
outer caller zeros the whole plan before lock acquisition.

The target-death wrapper uses a local plan and finishes it immediately under
its existing object-freeing graph lock. This preserves its current
reverse-index/self-reference transaction; the delayed metadata finish is a
normal-remap-only claim.

## Frozen source identity

```text
41cf1ba3d4f31678234ab1e3aa36526bb5cccbd84062aa82e566ebfd4adcdd06  pcc/py_runtime/src/py_gc_backend.c
14bbe2d02cee63f00f394539e80cc8fe8f46aa1914d8c9ef554d6f21f1459d84  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
8c09042de4cc7935bf088f27a3ecababc25e6917fc9b64ee1cccc3d628df241e  pcc/py_runtime/py/freestanding_gc_forwarding_identity.py
ea3bb0ec38b7f99a3af930f7749deedc712a594b9a28a6676cb9328254268b0e  pcc/py_runtime/py/freestanding_gc_object_nodes.py
89459617f42606a98706a1ee7372951d3ff5245792b3f8a75817dca6feb1dce6  pcc/py_runtime/py/freestanding_gc_relocation_drain.py
b2898d35098fc6583b5b6ac5b6b9ac1813945c965389c86735274e7faecfddfd  pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py
6404b753b819d05eb36f7c31a0c4dc5139bb299b74bef6eddfc6a8272c10d639  pcc/py_runtime/py/py_gc_backend.py
61867b8fafd2db19f56f5088d03c783e5be4fa152a4045b9b9c7b0c1f49d6c8b  pcc/py_frontend/codegen/runtime_abi.py
e1860bcef61d523f1c1057a7f4c1697dc71742b83e931ff774b21c8247edca70  tests/python/test_freestanding_gc_forwarding_retirement.py
426692b3dd62154fbbc0eefed01d1b94ac3caaeb0108ee16fc025424de230b59  tests/python/test_freestanding_gc_forwarding_identity.py
23cac4ffd51f826fd500691a12aa070e2e583375c35c26fe48c65e8ace422613  tests/python/test_freestanding_gc_object_nodes.py
87e2d10e59b55e5acc639725911281b4d9032caa0d55bcebed4fd6f750b143e8  tests/python/test_freestanding_gc_relocation_drain.py
4c04270684baab3356533cb89c6d16bb03c09870c56246a82ad14e3f5b8a0931  tests/python/test_freestanding_gc_barrier_dispatcher.py
3ba7d6e311f97a3e413c4f2f4fdacf58582528cc17fccf8c3569773333a1954a  tests/python/test_gc_update_referents.py
```

These hashes remained unchanged through the final dynamic packets.

## RED and implementation

The source/ABI/order regression was first run against normal remap's direct
`_retire_forwarded_source(old)` call and failed (`1 failed in 0.11s`). The old
helper called identity removal, which freed its node, and object-node release,
whose saturated pool path freed the node, before returning to the graph-lock
holder.

Identity removal now composes a detach owner and finish owner. Normal source
retirement chains the detached identity and object nodes into its mandatory
finish plan after index/provenance/list invalidation. The generic finish clears
the plan before invoking the identity and object-node owners outside the lock.
The legacy target-death wrapper composes the same transition with immediate
finish and therefore retains its established timing.

The final regression pins the 32-byte C plan ABI, all four strict caller
initializations, absence of `free`/legacy release in normal metadata detach,
and owner-specific post-unlock finishes.

## Focused gates

Ten LLVM/self strict closures across forwarding retirement, forwarding
identity, object nodes, object/page drain and barrier dispatch:

```text
10 passed in 6.19s
```

Log: `build/gc4-a3b-normal-remap-metadata-finish-closure.log`, SHA-256
`ccc59d30e89369796b46ec6f0a637b07259c18922a845d3242fe8b3aeb36c4a0`.

Complete five-owner source, closure, archive-owner and C/strict differential
packet:

```text
36 passed in 140.11s
```

Log: `build/gc4-a3b-normal-remap-metadata-finish-source-owner.log`, SHA-256
`dff99490fe50fb17feebd2a2e20e39972f129764418a618aa2e334352798e248`.

Strict cold raw-payload archive node:

```text
1 passed in 124.60s
```

Log: `build/gc4-a3b-normal-remap-metadata-finish-exception-strict.log`,
SHA-256
`8e42a5b0617116424b0a6f59fa98ddf7b9bee3123cc480b0f16fb74d36b63612`.

All fourteen C/strict type-specific raw-payload cases plus relocation-payload
ownership neighbors:

```text
21 passed in 13.99s
```

Log: `build/gc4-a3b-normal-remap-metadata-finish-behavior.log`, SHA-256
`ef08f08ab5e08a478d61db33de0ca3cb2193de9d695f7baed0d0fcb066f0a131`.

Fragmentation, stable-ID, C/strict target-phase-reset and GC3 oldification
compatibility:

```text
9 passed in 23.60s
```

Log: `build/gc4-a3b-normal-remap-metadata-finish-compatibility.log`, SHA-256
`eaa2ba546b2e78f01fc898ea668b1a4a3b377656cc05269d30285a83363834b1`.

Sixteen source/root-slot contracts passed before the long gates. Python
byte-compilation passed for all affected strict/ABI/test files. C syntax passed
with `PCC_WITH_THREADS=0` and `=1`; both reported only the same five
pre-existing unused-static-helper warnings. `git diff --check` was clean.

## Open boundary

`pcc_gc_relocation_retire_source_payload` remains entirely under the normal
remap graph lock. It still allocates record/context/side-table plans, detaches
and frees raw payload storage, releases side-table tokens, decrefs every saved
owned slot and frees its plans there. Moving it requires a prepare/commit/finish
contract that keeps the source and forwarding edge stable and preserves
failure-before-mutation behavior.

Target-death payload/metadata cleanup remains a separate transaction with an
OWNED self-reference constraint. Structural index/granule loops, the
baseline-red stale-candidate/fairness case, raw copy, mutator admission and
source lifetime, lock-free readers, concurrent/nested drains, ABA/backend
switching, callbacks/raw leases, resurrection, physical movement, A3c, broad
parity, performance and fixed point remain open. No bootstrap chain, broad
default suite, performance gate or five-GC matrix was run. The parent task
remains `IN_PROGRESS`.
