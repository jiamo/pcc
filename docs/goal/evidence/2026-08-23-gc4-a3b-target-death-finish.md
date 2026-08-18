# GC4 A3b target-death finish

Date: 2026-08-23

## Claim

The C transition oracle and strict freestanding pcc-Python runtime now use one
caller-owned 48-byte finish plan when `pcc_gc_note_object_freeing` removes
forwarding edges whose target is already dying.  Under the GC graph lock the
transaction removes the reverse target index, detaches the source payload and
source side tables, removes the source index and main edge, retires source
identity/object metadata, and chains the dead-target forwarding node.  The
caller then unlocks before freeing raw payloads and detached metadata/nodes or
releasing any saved non-target ownership.

Saved source-slot and source-side-table tokens equal to the dying target are
discarded rather than decrefed.  Dead-target forwarding nodes likewise finish
without the ordinary target decref.  This preserves the target's logical
zero/deallocating state while a non-self source-owned child still loses exactly
one reference.

This is a graph-lock reentry/lifetime result.  It does not prove stopped-world
ownership or raw-mutator quiescence: payload preparation and structural detach
still run under the graph lock without a collector-owned stopped-world epoch.

## RED chronology

The requested dynamic self-reference/control probe was added first against the
public forwarding and `pcc_gc_note_object_freeing` boundaries.  Both the C and
strict pcc-Python default builds already produced the requested terminal output
before the source change, so a visible dynamic underflow reproduction was
**DENIED** and is not described as RED.  The default runtime suppresses an
invalid decref at logical zero, so that result did not establish safe ordering.

The source/ABI/order contract was genuinely RED on the former one-argument
target-removal ABI and immediate payload/metadata/node finish:

```text
1 failed in 0.13s
```

It now proves both mirrors make the source edge non-resolving before deferred
payload finish, pass the dying target as the decref exclusion token, and consume
the caller finish plan only after graph unlock.

The first 120-second dynamic archive attempt timed out without a pytest summary
and was discarded; process inspection found no leftover pytest/compiler/probe
children.  The next cold build exposed a real strict compile error because the
shared payload helper lacked a freestanding owner annotation (`1 error in
105.34s`).  After exporting that local implementation, both LLVM/self closure
tests passed.  A first complete-gate attempt then caught a multiline decorator
that the source-owner test did not recognize; converting it to the canonical
single-line form made the 16 non-archive nodes pass before the final cold run.

## ABI and ownership shape

`PccGcBackend4RemapFinish` and its strict mirror now occupy 48 bytes.  Existing
page, ordinary-forwarding, identity, object-node and payload-plan fields remain
at offsets 0/8/16/24/32; offset 40 owns dead-target forwarding nodes.

The payload plan carries a nullable decref-exclusion token.  Normal remap passes
NULL and preserves its prior release behavior.  Target death passes the dying
target and skips only equal saved ownership tokens in both source-slot and
source-side-table finish loops.  The main target-death edge is cleared and
chained separately from ordinary forwardings, whose target ownership must still
be decrefed.

## Frozen source identity

```text
e5846b89500a0416fd841fdd0216a595a953a619c8240b0c3a4caa695196ac13  pcc/py_runtime/src/py_gc_backend.c
07cb7e2fcd8425c5ee4f1758338b6faca7adaf2e59f9b5d4f25ccf3d99546382  pcc/py_runtime/include/py_runtime.h
ea34d019188ad5473ab7b301049f507f95f7a38794fb724e03575c96cb595cda  pcc/py_runtime/py/py_gc_backend.py
1b6707cb28031c9ed9a748da26621f97922aae69ae3a2c4876ec8cc8465a708e  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
b5932a6380b8c549041fc4a3110ee0d7316f255130559b12e0f948de7e0aae62  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
72e64d097dc8b99801dccfdcb1519d0b2a0bcec6af3f9817656748435af2950f  pcc/py_runtime/py/freestanding_gc_relocation_drain.py
1dfcf117e8a196bcde542aa1b8ae415c617a9903ea025c358df5a8994572597c  pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py
0eb2e55ebda804f7095b9a5391bfc89ba0afc7a25c50cccdc57efa10b18790bb  pcc/py_frontend/codegen/runtime_abi.py
b37a98376dd68bb303320708d9fcc9c81ed84fd0d2faa6ac32c801a1b0aa9366  tests/python/test_freestanding_gc_relocation_payload.py
21a72219b2a0627c4b1a01ed578877b6d12968e88cc28f366674b95fa887fd1d  tests/python/test_freestanding_gc_forwarding_retirement.py
```

## Focused gates

The task-card payload/forwarding gate, including the final-source C and strict
self/control probes:

```text
21 passed in 126.19s
```

Log: `build/gc4-relocation-mutator-quiescence.log`, SHA-256
`e4e3015e2c6b1db28d2e28b959f734593363991e9caeb0693d9c8e2c93112fe6`.

All fourteen C/strict type-specific raw-payload cases:

```text
14 passed in 135.86s
```

Log: `build/gc4-a3b-target-death-raw.log`, SHA-256
`9180c226476e522d57ff8e6ea45778caecabc6a617dc00a74bc01acc2a0d16fa`.

Fragmentation, stable ID, both target phase-reset roots and GC3 oldification
compatibility:

```text
9 passed in 23.62s
```

Log: `build/gc4-a3b-target-death-compatibility.log`, SHA-256
`a86b7c2858d6da0266ece076a5f808c7380b1b2850744eae80360937a9fa20c2`.

Sixteen non-archive source/ABI/order/LLVM+self closure nodes passed in 3.19s.
All changed Python files byte-compiled; C syntax passed with
`PCC_WITH_THREADS=0` and `=1`; `git diff --check` was clean.

## Open boundary

`pcc_gc_note_object_freeing` still composes outgoing-source
`pcc_gc_forwarding_remove(o)` immediately under the graph lock.  That distinct
path removes a live target edge and decrefs the target, so it must be audited
before sharing either the ordinary or dead-target finish chain.  More
fundamentally, none of these finish splits proves that target death or normal
remap cannot overlap a pre-existing raw list/dict/set access.  Stopped-world
ownership, raw-access/no-park admission, graph-lock depth, remembered-root
admission, source lifetime/ABA, callbacks/raw leases, resurrection, physical
movement, A3c, broad parity, performance and fixed point remain open.  The
parent task stays `IN_PROGRESS`.
