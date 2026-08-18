# GC4 list callback-free raw transactions — 2026-08-24

## Claim

The callback-free list core now keeps raw list state and returned/temporary
ownership coherent across moving-GC safepoints in C and strict pcc-Python:

- `get`/`getitem` perform owner reload plus slot load and prepared retain under
  one graph/no-park tenure, then finish retain diagnostics after unlock;
- exact-int getters convert the loaded value before unlocking;
- `set`/`setitem` root owner/item and use split store prepare/commit/finish;
- `pop` registers an initially-NULL updateable result root before the locked
  ownership-transfer/memmove, keeping the returned object canonical while the
  owner root is unregistered;
- `reverse` prepares two temporary retains and two store plans, commits both
  swaps under the graph lock, then finishes store/retain packets and drops
  non-final temporary refs after unlock; and
- `copy`/`repeat`/`concat` keep source/output roots and use owned transactional
  `py_list_get` plus `py_list_append`, rather than raw borrowed loads followed by
  an unlocked incref/store.

High-level append/insert/extend/strict `_push_to_list` were also corrected to
use `pcc_gc_store_ptr_plan_init` outside their outer graph lease, locked commit,
and post-unlock finish.  This removes a nested-lock bug where the full store
helper's finalizer/diagnostic tail ran while an enclosing list transaction still
owned the graph.

Backend0 retains dedicated direct bodies for all of these hot operations and
does not allocate/register roots, take the graph lock or allocate split plans
beyond the pre-existing direct refcount store path.

## Internal ABI

`pcc_gc_store_ptr_plan_init(plan, owner, backend)` now combines the existing
128-byte plan initialization with historical owner telemetry outside the lock.
C and strict cross-object users then call the already-proven owner-aware locked
commit and post-unlock finish.  The API remains internal/cross-object and absent
from the managed runtime ABI.

## Dynamic forwarding proofs

- A stale forwarded list owner is passed to get, reverse and pop.  C/strict
  return the correct owned value, reverse and transfer ownership on the target,
  preserve final target order/length and leak no temporary roots.
- A stale forwarded list source is passed to growth append/extend plus
  copy/repeat/concat.  C/strict use target contents and preserve exact values;
  temporary root counts return to the deliberate persistent baseline.

No production pause/test publication hook was added.

## Gates

- C syntax, threads off/on: pass (one pre-existing unrelated pointer warning).
- strict `py_obj.py`, `py_list.py`, `py_list_set_slice.py` self-backend,
  no-libpython closures: pass.
- source/ABI contracts: `14 passed in 24.73s`.
- final list/refcount/native/parity/forwarding behavior: `35 passed in 197.50s`.
- final stale-owner/source C/strict matrix: `4 passed in 1.86s`, plus extended
  copy/repeat/concat stale-source proof `2 passed in 1.20s`.
- five-GC abstraction plus ABI chunks: `17 passed in 10.73s`.
- task-card relocation payload/forwarding retirement gate:
  `24 passed in 143.96s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-list-callback-free-final.log`
- `build/gc4-list-callback-free-forwarded-final.log`
- `build/gc4-list-snapshot-copy-forwarded-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
2ffec7d2a397b296bcd01bd9c0934c2cc460d12fdd589b1ffc6bda1306c91a2d  pcc/py_runtime/src/py_obj.c
04475fe4ae8cd591d7545c6dff42755aab09aee8cd3882ab16f17d6373b90785  pcc/py_runtime/py/py_obj.py
57820e959383153b08853b194dd06c2363bd339b2514e6f31fb7b0a04d7271ea  pcc/py_runtime/src/py_list.c
e49ac879048de815a36b0a30fb88774fec43e687f9ff741357b8f959c252ca7b  pcc/py_runtime/py/py_list.py
26d5a2decf43c80e1d6f7d9d99bc8784f19b700a4648896546ad16e5be8da5f8  pcc/py_runtime/py/py_list_set_slice.py
5abbf8ebf3bebfa6f191bb5cf258d40162428bb7c722a99a6c3d80d3eadd8183  pcc/py_runtime/src/py_internal.h
92a4327020484e0368db07dc8ab4e9c9d0371555291f1fb71747bb37987a5904  pcc/py_frontend/codegen/runtime_abi.py
a7e6ddad19a5866bb50d10a1cd90e6c6bce47a0e2ef3bf4a163abb02e0c7bed5  tests/python/test_gc_codegen_write_barrier.py
f030eb0c5fb7426f6ecb0e90b37d052d3a3473dfed7dbff18568ff091a1de989  tests/python/test_gc_threading_substrate.py
27bc2a5ade7ee2974f61a8fe2290f142584376ca98fd27597c5bc5c56272f5de  build/gc4-list-callback-free-final.log
5ab2faec4f3e169917d77899bac676d0e581bb5d2b8dff6c41777a8b75f05061  build/gc4-list-callback-free-forwarded-final.log
8571ad7e0b73caa8d52a9dcda9a436258fc1f2505a3f54b92dc2115ab7949544  build/gc4-list-snapshot-copy-forwarded-final.log
03ef3ed70f8b7d96ca07b18cfd284c89ed826f6d3acdf7c4892b9a7388d704ff  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for callback-free list get/set/pop/reverse/copy/repeat/concat
transactions and nested split-store finish ordering.  Parent task remains
`IN_PROGRESS`; equality/callback loops and decref/finalizer-bearing destructive
mutations remain separate.
