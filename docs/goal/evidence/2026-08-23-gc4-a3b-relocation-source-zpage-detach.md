# GC4 A3b relocation source-ZPage detach tail

Date: 2026-08-23

## Claim

For one stable Backend 4 selection and a valid managed relocation candidate,
the final public relocation-copy commit in both the C transition oracle and the
strict freestanding pcc-Python production runtime no longer frees the source
ZPage owner node or any of its payload-span nodes while holding the GC graph
lock.

The locked transaction now unlinks the source owner, removes its owner-index
entry, updates payload-span and page accounting, and stores the still-owned
detached ZPage node in a mandatory relocation finish plan.  After graph unlock,
the ZPage lifecycle owner frees the detached payload-span chain and source-owner
node.  The C finish structure and strict stack plan are both 24 bytes, with the
source ZPage node at offset 16.  Both private commit helpers reject a missing
finish plan; the strict NULL-plan fallback frees are absent.

This evidence narrows an over-broad statement in the preceding raw-payload
preallocation slice.  That slice proved that target payload-span publication
consumes preallocated nodes, but it did not remove the source ZPage removal
path that freed already-linked source span nodes under the final lock.  This
slice closes that source-owner tail only.

## Frozen source identity

```text
594407c77cdaa4e42c4209737864a1fe4c021cf5aa8f51e9115d939cc7c586d9  pcc/py_runtime/src/py_gc_backend.c
e5417f52b73139f1cf9ce0fe3cb2d993609f255262e971ee7c954e51aaa8833d  pcc/py_runtime/py/freestanding_gc_zpage_lifecycle.py
4f0d2afb8333b1de1dfe5f239f0d284518a58558106f792cb0b001305f6a884a  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
21320efd2c1c483f799d302a0d6d66adfe8b55d2bfc976363b1049128fd2bc98  pcc/py_frontend/codegen/runtime_abi.py
74486d852585140e0a3b815d72d70b1a4ac1901d35320ecafd176154ed3e8189  tests/python/test_freestanding_gc_relocation_copy.py
aa948b68b39b59dddb950545b5bb08e39ee26a19346f8f47ac19464e0abf07da  tests/python/test_freestanding_gc_zpage_lifecycle.py
```

The runtime hashes were captured before the final task-card gate and remained
unchanged through all dynamic tests.  The relocation-copy test hash changed
after the long gates only because two newly added assertions were line-wrapped;
the exact formatted source regression was rerun and passed in 0.12 seconds.

## RED and implementation

The new source/ABI/order regression was first run against the direct-remove
shape:

```text
gtimeout 30s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_freestanding_gc_relocation_copy.py::test_relocation_copy_defers_source_zpage_free_after_graph_unlock
```

It failed before implementation with a `KeyError` for the absent
`pcc_gc_backend4_zpage_detach_for_relocation` ABI entry (`1 failed in 0.14s`).
The test pins all of the following:

- the locked commit uses detach, not the legacy remove operation;
- the detacher has no `free` or owner-node release;
- both finish plans have the same size and offset contract;
- finish cleanup runs only after graph unlock; and
- neither mirror retains a NULL-plan fallback cleanup path.

The implementation splits source ZPage removal into detach and finish owners.
Legacy non-relocation removal keeps its existing behavior by composing the new
detacher with its existing span cleanup and node-pool release.  Relocation uses
the new finish owner, which frees rather than pooling the detached node after
unlock, so it cannot re-enter the lock merely to mutate the node free list.

Strict closure validation also exposed a pre-existing ownership-test drift:
seven selector globals referenced by the HEAD freestanding ZPage source were
missing from `RAW_GLOBAL_IMPORTS`.  The focused test manifest now lists those
existing globals.  This is a test-only closure repair, not a new runtime global
or part of the relocation claim.

## Focused gates

Exact formatted source regression:

```text
1 passed in 0.12s
```

Full relocation-copy plus ZPage-lifecycle source/LLVM/self closure,
source-contract, differential and production archive-owner packet:

```text
gtimeout 630s zsh -o pipefail -lc \
  'gtimeout 600s env -u LC_ALL uv run pytest \
  tests/python/test_freestanding_gc_relocation_copy.py \
  tests/python/test_freestanding_gc_zpage_lifecycle.py \
  -vv -x -n0 --tb=short | tee build/gc4-a3b-zpage-detach-source-owner.log'
17 passed in 134.86s
```

Log SHA-256:
`2b3e2bd5a07e513afaa8312f62fbbdd0ccc934c6d9947434783b59de02fecf60`.

All type-specific raw-payload cases in the C and strict runtime roots:

```text
tests/python/test_gc_backend4_production.py::test_backend4_relocation_copies_type_specific_raw_payloads
14 passed in 4.20s
```

Log: `build/gc4-a3b-zpage-detach-raw-matrix.log`, SHA-256
`6c518bd3f22d155f26194042f316b892245986ac69cbb698b497ff39d08dd381`.
The individual C multi-span class node passed in 7.21 seconds; the cold strict
counterpart passed in 125.81 seconds.  Their logs are respectively:

```text
36c2532f57907c9912fc7a114d6467b73b7524f823d8ef8a3bb716450a96d667  build/gc4-a3b-zpage-detach-class-span-c.log
cc5035a5ae256e70730148dc7b7dcc4c795ee73fc8697010fbe926c62039e84f  build/gc4-a3b-zpage-detach-class-span-strict.log
```

The task-card payload plus forwarding-retirement pair ran with node IDs and a
durable live log:

```text
gtimeout 630s zsh -o pipefail -lc \
  'gtimeout 600s env -u LC_ALL uv run pytest \
  tests/python/test_freestanding_gc_relocation_payload.py \
  tests/python/test_freestanding_gc_forwarding_retirement.py \
  -vv -x -n0 --tb=short | tee build/gc4-a3b-zpage-detach-payload-retirement.log'
14 passed in 5.29s
```

Log SHA-256:
`af765ae3c3148da585d18b22e7fb9011fd055ab535b24af79497839985155ca6`.

`python -m py_compile` passed for the three changed Python runtime/ABI files and
two focused test files.  C syntax passed in both modes:

```text
gtimeout 30s cc -O2 -fPIC -Wall -Wextra -std=c11 -pthread \
  -DPCC_WITH_THREADS=1 -Ipcc/py_runtime/include -Ipcc/py_runtime/src \
  -fsyntax-only pcc/py_runtime/src/py_gc_backend.c

gtimeout 30s cc -O2 -fPIC -Wall -Wextra -std=c11 \
  -DPCC_WITH_THREADS=0 -Ipcc/py_runtime/include -Ipcc/py_runtime/src \
  -fsyntax-only pcc/py_runtime/src/py_gc_backend.c
```

Both modes emitted the same five pre-existing unused-static-function warnings
and no new warning.  No broad default suite, bootstrap chain, performance run,
fixed-point gate or five-GC matrix was run for this finite correctness slice.

## Open boundary

This does not prove that the complete final relocation-copy commit is free of
allocation, freeing, decref or arbitrary-delay work.  In both mirrors,
`pcc_gc_install_forwarding_unlocked` still allocates identity and forwarding
nodes, may allocate/rehash/free three pointer-index tables, and contains cleanup
decrefs on failure while the graph lock is held.  That forwarding/identity
preparation is the next separately reviewed A3b boundary.

The ZPage detacher still performs structural/accounting loops and tripwire
checks under the graph lock, and raw byte copying remains locked.  Raw mutators
do not yet participate in relocation phase/no-park admission.  Selected-source
and page lifetime across unlocked planning, index failure rollback, nested
callers, concurrent drains/collectors, destroy/reuse ABA, remap/retirement,
target death, GC3 compatibility, callbacks, C-API raw views and leases,
resurrection, physical movement, A3c integration, performance, fixed point and
broad five-GC parity remain unproved.  The parent task remains `IN_PROGRESS`.
