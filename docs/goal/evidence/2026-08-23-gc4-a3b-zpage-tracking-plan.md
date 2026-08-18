# GC4 A3b ZPage tracking plan

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b sub-boundary confirmed; parent task remains `IN_PROGRESS`.

## Claim boundary

Backend 4 object registration in the C and strict pcc-Python runtime roots now
prepares its ZPage node, owner-index capacity and any fallback metadata
page/backing span outside graph-lock ownership.  Under the lock it revalidates
the node pool, index load and page choice, commits a still-needed index table,
and links through allocation-free primitives.  A prepared free page that loses
the revalidation race returns to the free list; unused fresh page/span and
losing node/index preparations retire after unlock.

The raw allocator's `pending_alloc_count` handoff is unchanged: registering an
object already allocated in a ZPage finds that page and consumes one pending
reservation.  A malloc-backed object with no ZPage origin can still enter the
fallback metadata path.  Failure to prepare its page/span leaves neither an
object-index nor a ZPage-owner-index entry.

This is not A3c and is not a mutator-quiescence claim.  The allocation-capable
legacy tracking entry remains as a compatibility surface but is no longer
called by object registration.  Relocation-reset retirement, GC3 promotion and
remembered-owner safepoints/decrefs, extension/caller root callbacks,
tripwire/log or unbounded holders, raw container transactions and the
collector-owned stopped-world phase remain open.

## Genuine RED

`test_object_registration_prepares_zpage_tracking_before_graph_lock` was added
before implementation.  It failed while locating
`_backend4_zpage_node_prepare()` in the strict registration body:

```text
1 failed in 0.09s
ValueError: substring not found
```

The now-green contract checks both runtime roots, exact cross-object ABI
signatures, and every node/page/table allocation call in registration against
the latest graph unlock.  It also rejects the old allocation-capable tracking
call from that critical section.

## Implementation

- ZPage nodes now expose prepare, locked pool-need and allocation-free take
  operations while retaining the bounded existing node pool.
- The ZPage owner index now exposes capacity planning, allocation-free commit
  and allocation-free upsert; replaced tables return to the caller for
  post-unlock release.
- Fallback metadata pages may be detached under lock but are reset and given a
  backing span only after unlock.  Revalidation either consumes the complete
  page, restores a detached free page, or releases an unused fresh page after
  unlock.
- `pcc_gc_backend4_zpage_track_alloc_preallocated` links only complete node,
  index and page state and performs no allocation.
- C declarations and strict cross-object signatures are exact mirrors.

## Focused evidence

All pytest commands stopped at the first failure.  The long current-source
packet used visible node IDs, short tracebacks and a durable live log.

1. Exact ZPage allocation/mechanics/index source, LLVM/self, C-oracle,
   production archive, fallback/failure and 16-way true-pthread packet:

   ```text
   gtimeout 90s zsh -o pipefail -c 'gtimeout 60s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_freestanding_gc_zpage_allocation.py tests/python/test_freestanding_gc_zpage_mechanics.py tests/python/test_freestanding_gc_index_table.py 2>&1 | tee build/gc4-a3b-zpage-track-final.log'
   24 passed in 10.20s
   ```

   The content-addressed nonthreaded and threaded archives had already been
   constructed by the same frozen runtime source.  The first complete cold
   packet passed the same 24 nodes in 261.97s; the command above recaptured the
   final test-file identity after the last ABI/failure assertion was added.

2. Current production archive tracking parity across GC0 through GC4:

   ```text
   gtimeout 420s zsh -o pipefail -c 'gtimeout 390s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_freestanding_gc_tracking.py::test_production_archive_uniquely_owns_tracking_and_matches_c_oracle_gc0_to_gc4 2>&1 | tee build/gc4-a3b-zpage-track-gc0-gc4.log'
   1 passed in 1.14s
   ```

3. The direct current C runtime owner-index neighbor passed `1 passed in
   0.36s`.  Strict `py_gc_backend.py` and
   `freestanding_gc_zpage_allocation.py` each compiled under
   `--backend self --python-libpython=off --ir-scaffold=on --python-library`.
   Python syntax, C syntax with `PCC_WITH_THREADS=0/1`, and
   `git diff --check` passed.

Two early discovery commands used 150/120-second budgets that were below the
cold content-addressed archive envelope and ended without a pytest summary.
They were not counted as green evidence; process checks found no surviving
pytest, make or compiler child before the measured packet was rerun.

## Frozen identities

```text
8a7d17f74a34c1a8fbb65968c6e6cec34e0bafa00b2e287da8b3adc475d8b01d  pcc/py_runtime/src/py_gc_backend.c
51a7bfc98b139e50cb6c6d5e66641631de6286e562928ea15df49ea6eb7ba077  pcc/py_runtime/src/py_gc_index_table.c
8255502d54b871de9e25112c44c72663dca4d7912318ef2da9734b6ac0cb5296  pcc/py_runtime/src/py_internal.h
25568696973bbdf28bde63ce796c4ca087def12a258556a705e5f8eeef31c8ef  pcc/py_runtime/py/freestanding_gc_index_table.py
832d83cb2da405516b91d553fd1ceafd2183de59ead25e19120f70001f1947a5  pcc/py_runtime/py/freestanding_gc_zpage_mechanics.py
6e00fa58adf005851429c2d9d28a10c4861400d2a6132cff511955714a57fadc  pcc/py_runtime/py/freestanding_gc_zpage_allocation.py
2136881501aced355bf0697529b12e61a171dffa5cc130aa7e97315bd4b5e3c1  pcc/py_runtime/py/py_gc_backend.py
de720fb9f89c16b60cc11f5c8052ad5b09771fbccbce0506d476f4231ec613fb  pcc/py_frontend/codegen/runtime_abi.py
b0c4d34d4fc81ca660f470b07a9c972c73b0db49ef6e5e54949e5cf1ad7984fa  tests/python/test_freestanding_gc_index_table.py
8cdcd53157cb49bc11900fa33a1849c0bc45644ec46735b0028e639493886e79  tests/python/test_freestanding_gc_zpage_mechanics.py
d60ce3623aa1573ab17c1c918b84a66759ff8b227c87a82627b99c68b3ae884c  tests/python/test_freestanding_gc_zpage_allocation.py
4d43911397877c591e05479eadb8ec8e720ed63900371760b2bb3173f89e01d5  tests/python/test_gc_backend4_production.py
753ded54d4558c5fd3e06c7319d306d566ce795a16166afcf6399719a8a13443  build/gc4-a3b-zpage-track-final.log
a0dba67bbaa50dada42a6aa75930a32b7385dc14fcf5a429589bc33970be24d9  build/gc4-a3b-zpage-track-gc0-gc4.log
```

## Next boundary

Split relocation-set/reset and evacuation-list retirement so all frees and
unbounded list consumption occur after graph unlock in both runtime roots.
Preserve candidate flags, evacuation-page membership, reseed counters, nested
or concurrent reset semantics and allocation-failure behavior.  A3c remains
blocked until that and the remaining GC3/callback/log holder inventory are
source- and pthread-green.
