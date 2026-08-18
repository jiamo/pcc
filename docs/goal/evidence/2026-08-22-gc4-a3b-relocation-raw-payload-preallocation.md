# GC4 A3b relocation raw-payload preallocation

Date: 2026-08-22

## Claim

For one stable Backend 4 selection, threads enabled, default `ATOMIC`
refcounts, valid managed source objects and the six type-specific raw payload
families below, public relocation copy in both the C transition oracle and the
strict freestanding pcc-Python production runtime no longer allocates or frees
raw buffers or ZPage payload-span nodes while holding its own final GC graph
lock:

- continuation stack chunk and slot array;
- exception traceback records;
- class bases, MRO, method table and field-name table;
- dict indices and entries;
- set entries; and
- list items.

The public transaction snapshots raw metadata under a short graph-lock scope,
allocates every raw buffer and required ZPage span node after unlock, allocates
the pinned relocation target after unlock, then enters one final graph-lock
scope.  That scope re-snapshots and validates the source metadata, checks the
target page has capacity for the complete span bundle, copies bytes, publishes
the preallocated buffers and span nodes, and completes the existing slot and
forwarding transaction.  Structural plan cleanup and failed-target decref run
after graph unlock.

The final publish helpers contain no `malloc`, `calloc` or `free`.  If final
validation fails before publication, plan finish owns and frees all buffers and
span nodes outside the lock.  If an unexpected later span publication fails,
all raw buffers have already been attached to a dealloc-safe target and their
plan pointers are cleared; target cleanup owns those buffers, while plan finish
frees only still-unlinked span nodes.

Both implementations fail closed before reading type-specific fields when the
requested object size is smaller than the corresponding layout.  The strict
snapshot also reloads the current object tag before interpreting the saved tag.
The strict raw plan is pinned at 416 bytes with four 64-byte descriptors
starting at offset 152; each descriptor's prepared buffer and span-node slots
are at offsets 48 and 56.

## Frozen source identity

```text
9f494afcd86b7351199f6db004e54e131a9afa920cf7973e9949d0106a0d5467  pcc/py_runtime/src/py_gc_backend.c
0c11b02a74094be8cef6bc81e46b52c74204acd42cc7bcf8319fb5417847124f  pcc/py_runtime/py/py_gc_backend.py
c6c76397545531824cf29e2f19e75c9981a1031de36ef0fe57998243155b0cdf  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
a696390d161d93c7c5efd1b82efc36db4bde492c260b124bb6be873ff8ecd317  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
5a8babcdc0c1663199a1268da18f7551da39ea2f3016a8bd54a446ae99053cd7  pcc/py_frontend/codegen/runtime_abi.py
ec25059c47a716fdc2d6212e250d77d91eeb2990c589de4b3a320167fd4d5dff  tests/python/test_gc_backend4_production.py
172f3447ba349ee20a0e315e93ed22f616febe8ce489234f7cc4b907ca9b6f24  tests/python/test_freestanding_gc_relocation_copy.py
a08ff3f4f393840fba36165723ec95f3c5cf405ec0ba076e9e5693c11b6bd60e  tests/python/test_freestanding_gc_relocation_payload.py
```

The hashes were captured before the final strict archive build and matched
after it.  Whole-worktree `git diff --check`, isolated Python compilation and
both threaded and threads-off C syntax checks pass.  Each C syntax mode emits
the same five pre-existing unused-function warnings and no new warning.

## RED and review corrections

The first dynamic minimum-layout regression failed against the C oracle:
`pcc_gc_relocate_copy(exception, 16)` returned a non-NULL undersized target,
and the probe exited 15 (`1 failed in 0.74s`).  Exception, class,
continuation and list snapshots now reject undersized targets before their
first type-specific field access, and the corrected exact C exception node
passed.

A later local adversarial pass found the same ordering defect still present in
the dict and set snapshots: both read `capacity` before their minimum-size
check.  The new source-order regression failed with `120 < 80`
(`1 failed in 0.12s`).  C and strict snapshots now check dict/set layout size
first; the exact regression is green.

The same pass found that strict validation trusted the tag saved before the
unlocked allocation window.  Its source-order regression failed because the
tag guard was absent (`1 failed in 0.12s`).  Strict now reloads and compares
the tag before clearing or interpreting the raw snapshot region.  The focused
test is green.

The dynamic matrix was extended with real dict and set buffers.  It proves
that indices/entries are distinct allocations, capacities and used counts are
preserved, copied bytes match, candidate state survives an undersized failed
copy, and the moved managed-entry span has a valid ZPage card in both runtime
roots.

## Final focused gates

The combined current-source copy/payload source, LLVM/self closure, plan-shape
and production archive-owner packet reports:

```text
14 passed in 4.40s
```

The combined six raw families under both the C transition oracle and strict
pcc-Python runtime, plus the two owned-slot retain neighbors, reports:

```text
14 passed in 4.78s
```

Before the final archive build, the same non-archive static/C packet reported
`19 passed, 2 deselected in 4.42s`.  The final strict dict node was the cold
current-source build gate:

```text
test_backend4_relocation_copies_type_specific_raw_payloads[dict-5-pcc_python]
1 passed in 137.71s
```

Log: `build/gc4-a3b-raw-payload-final-strict-cold.log`, SHA-256
`7c464e1dc88a8e1a625fb6d7d6d08a241ba909ac4a04a99aa217ab5e72c1bd80`.
The five remaining strict raw nodes plus strict slot-retain passed
`6 passed in 3.52s`; the two archive-owner nodes and strict deallocating-copy
failure neighbor passed `3 passed in 0.92s`.

No broad default suite, stage/bootstrap chain, performance profile or five-GC
matrix was run for this finite correctness slice.

Task-board validation reports `OK: 382 tasks validated`, and
`render-startup --check` confirms `docs/current-goal-state.md` matches the
generator.  The combined goal-state/startup-doc test intentionally stopped at
the repository's already registered startup-size failure: the generated file
is 33,149 bytes versus the 20,000-byte bound (`1 failed, 8 passed`).  This is
routed by `GOAL-P0-STARTUP-STATE-BOUNDED-RENDER`, which depends on the current
GC4 task; it is not a green gate or a regression claimed fixed by this slice.

## Strict archive receipt

The final cache key is
`477af77692f4dd15ab52a1d4-threaded-pcc-py`.  The production provenance verifier
passed against the frozen current `pcc/py_runtime` source root.

```text
92095a09965daae034be3f139cc02d343c8ecab2f055267d98a596207fc73378  libpy_runtime_pcc_py.a
6b1f655d592d9858f0214eace5b49adf57aef740d8667e1f605eebd7988bafe1  libpy_runtime_pcc_py.a.provenance.json
71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda  libpy_runtime_pcc_py.a.capi_syms
1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1  libpy_runtime_pcc_py.a.target
fbbd02b377fe6421e0e8893996ce8ed0950f5c864b854f2d12b7f8c42a6ce5a0  .pcc-threaded-pcc-py-complete
```

The provenance is schema `pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target
`arm64-apple-darwin25.5.0`, 186/186 pcc-Python members, zero host-C-compiler
members and 444 C-API symbols.

## Review and open boundary

This final review was performed locally in accordance with the user's request
to avoid sub-agents.  No sub-agent was started or contacted and no independent
review verdict is claimed.

This slice moves allocation and structural cleanup out of public GC4 copy's
own graph-lock scope; it does **not** make byte copying or span-list publication
lock-free.  Raw mutators do not yet participate in the relocation phase/no-park
protocol, and selected-source/page lifetime is not protected across the
unlocked planning windows.  The private strict five-argument commit helper
still relies on its internal graph-lock precondition.  GC3's compatibility
wrapper still prepares and finishes the shared plan inside its existing
generational holder.  Allocation-failure injection is not available, so
rollback ownership is pinned by source/control-flow contracts plus ordinary
failure neighbors rather than a dynamically forced allocator failure.

Forwarding/identity-index/ZPage commit, final remap and retirement,
remembered-root admission, nested outer-lock callers, concurrent drains,
source/page destroy-reuse epoch/ABA, stale-candidate fairness,
constructor-publication races, remaining in-lock loops/logging/tripwires,
CMS queue/thread-exit boundaries, unlocked public decref synchronization,
callback roots, C-API raw views and buffer leases, target-death cleanup,
resurrection restoration, physical movement, A3c no-park integration,
`BIASED`/`DEFERRED` parity, stage/performance, fixed point and broad five-GC
parity remain open.  The parent task remains `IN_PROGRESS`.
