# GC4 A3b normal-remap forwarding-edge finish

Date: 2026-08-23

## Claim

In normal Backend 4 two-epoch remap retirement, both the C transition oracle
and strict freestanding pcc-Python production runtime now detach a forwarding
edge under the GC graph lock but defer the edge's retained-target decref and
forwarding-node free until after the outer object-drain, page-drain or idle-step
caller releases that lock.

Each caller initializes a 16-byte stack-owned remap-finish plan before locking.
The locked detach removes the source and reverse-target index entries, unlinks
the main forwarding edge, decrements forwarding population, updates the source
page's pending-forwarding state and chains the still-owned node into the plan.
After unlock, one finish owner first completes retained-page release and then
decrefs/frees every detached forwarding node. The node itself keeps the target
reference alive across the locked-to-unlocked handoff.

The legacy forwarding-remove ABI composes the same detach and finish helpers
immediately for its existing non-remap callers. Target-death cleanup is
unchanged: its reverse-index/self-reference contract differs and needs a
separate ownership proof.

## Frozen source identity

```text
af71acc939e80be6161ce905ce38578a4c60de6e213b5b9b595c86aab42fe53e  pcc/py_runtime/src/py_gc_backend.c
0f7adc4d029c7ff7b972787d92ba10ec5b7213e8890b75f73cb0289e08be4049  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
233d332c3f74722105027aea7db40f39e6efffb7764c39d628137a2df0ec1928  pcc/py_runtime/py/freestanding_gc_relocation_drain.py
be7f51650661e173a6bbc268261bcd5b0071ff2becbfbac07ab57177ce1a4f65  pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py
6404b753b819d05eb36f7c31a0c4dc5139bb299b74bef6eddfc6a8272c10d639  pcc/py_runtime/py/py_gc_backend.py
5fea187c70f77711499df51883c40bf519a7d77774340a7cb63503643d586524  pcc/py_frontend/codegen/runtime_abi.py
5e26cba7af178252b267b755c97338d948ec886405accc547ea6d46497058560  tests/python/test_freestanding_gc_forwarding_retirement.py
87e2d10e59b55e5acc639725911281b4d9032caa0d55bcebed4fd6f750b143e8  tests/python/test_freestanding_gc_relocation_drain.py
4c04270684baab3356533cb89c6d16bb03c09870c56246a82ad14e3f5b8a0931  tests/python/test_freestanding_gc_barrier_dispatcher.py
3ba7d6e311f97a3e413c4f2f4fdacf58582528cc17fccf8c3569773333a1954a  tests/python/test_gc_update_referents.py
```

These hashes remained unchanged through the final dynamic packets.

## RED and implementation

The new source/ABI/order regression was first run against the direct normal
remap path and failed on its explicit
`pcc_gc_forwarding_remove(old)` call (`1 failed in 0.10s`). That old helper
performed `py_decref(dead->to)` and `free(dead)` before returning to the
caller-held graph lock.

The final regression proves all three C and strict outer callers initialize
their finish plan before lock acquisition, invoke remap while locked and invoke
finish only after unlock. It pins the C plan size/offset ABI, forbids decref or
free in detach, requires decref-before-free in finish, and proves normal remap
uses detach rather than the legacy immediate remove composition.

The audit deliberately separated target-death cleanup. Historical source
payload evidence shows that route must make self-referential edges
non-resolving before releasing saved ownership. It therefore remains unchanged
and open rather than being silently generalized from the normal-remap proof.

## Focused gates

LLVM/self strict closure for forwarding retirement, object/page drain and the
barrier dispatcher:

```text
6 passed in 3.02s
```

Log: `build/gc4-a3b-normal-remap-edge-finish-closure.log`, SHA-256
`606d8ba0522430ec2508842d6ab4e97a2a32a46f020e64cc5fa3120ef732c902`.

Complete source, ABI, archive-owner and C/strict differential packet for the
three directly affected modules:

```text
25 passed in 135.57s
```

Log: `build/gc4-a3b-normal-remap-edge-finish-source-owner.log`, SHA-256
`e7706cca8412d762357c623749c2f1ad4266c6f050457862a8ea7b9e73d6168a`.

The strict exception raw-payload node was isolated to pay the known cold
runtime-archive build envelope:

```text
1 passed in 123.89s
```

Log: `build/gc4-a3b-normal-remap-edge-finish-exception-strict.log`, SHA-256
`bac0acd8bfc6e5e0d7c3ca1572f8073711fea47f9197d0242644bd525b529b29`.

With that cache warm, all fourteen C/strict type-specific raw-payload cases
plus relocation-payload ownership neighbors passed:

```text
21 passed in 6.98s
```

Log: `build/gc4-a3b-normal-remap-edge-finish-behavior-final.log`, SHA-256
`d1d630e7aa88f195bbfafa8affd73d51a5f93f84099727589a1ea161018e389e`.

Fragmentation, stable-ID, both C/strict target-phase-reset neighbors and GC3
oldification compatibility passed:

```text
9 passed in 24.81s
```

Log: `build/gc4-a3b-normal-remap-edge-finish-compatibility.log`, SHA-256
`724006554635030b17d048314da108b5c8643e5619c9ffa232867919b62d4f6f`.

An initial combined behavior command incorrectly used a 90-second watchdog
despite the documented 124--138 second cold strict node. It produced no
summary and is not evidence. The watchdog terminated it; immediate process
inspection found no surviving pytest, pcc1/pcc2/pcc3 or bootstrap child. The
complete isolated and warm-cache summaries above replace it.

Thirteen signature/root-slot/source contracts passed before the long gates.
`python -m py_compile` passed for all affected strict/ABI/test files. C syntax
passed with `PCC_WITH_THREADS=0` and `=1`; both reported only the same five
pre-existing unused-static-helper warnings. `git diff --check` was clean.

## Open boundary

This slice does not move source-payload retirement, identity removal,
granule/exact-provenance retirement or object-node release out of remap's graph
lock. Payload retirement still allocates its record/context/side-table plans,
frees detached raw bases and decrefs saved store-buffer/source-slot ownership
under that lock.

Target-death cleanup still performs its distinct payload/metadata/node cleanup
under the object-freeing graph lock. Its self-reference and exact ownership
disposition remain governed by
`docs/investigations/gc-backend4-forwarded-source-payload-retirement.md`.
The baseline-red stale-candidate/fairness case remains unchanged. Raw copy,
mutator admission/quiescence, source lifetime, concurrency/ABA/backend
switching, callbacks/raw leases, resurrection, physical movement, A3c, broad
parity, performance and fixed point also remain open. No bootstrap chain,
broad default suite, performance gate or five-GC matrix was run for this
finite correctness slice. The parent task remains `IN_PROGRESS`.
