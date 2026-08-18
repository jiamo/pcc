# GC4 A3b relocation owned-slot retain tail

Date: 2026-08-22

## Claim

For one stable Backend 4 selection, valid managed values, threads enabled and
the default `ATOMIC` refcount strategy, public relocation copy in the C
transition oracle and strict freestanding pcc-Python runtime no longer performs
owned-slot retain logging or payload-plan allocation inside its own graph-lock
scope.

The public transaction snapshots the exact source slot count while locked,
unlocks to allocate a zeroed slot plan and destination, re-locks to revalidate
the source slot shape, performs each real canonical retain before publishing
the corresponding destination slot, then unlocks before consuming retain
logging/diagnostic tokens and freeing detached structural nodes.  A failed
strict validation sees a zero-initialized two-pointer finish plan, so it cannot
read or free uninitialized stack data.

The historical two-argument strict
`pcc_gc_backend4_relocate_copy_unlocked` ABI and its managed caller were
removed.  The five-argument preallocated commit helper is private to the
freestanding cross-object ABI and absent from the public runtime signatures and
header.

## Frozen source identity

```text
d806407c183c31ad2887acc53466487b5d0f75ea331f155ae6e711b7357b9e40  pcc/py_runtime/src/py_internal.h
d22f34f6430225f1f6ad7336ada1e1c848806b49293d0e4f9ff4276d96698246  pcc/py_runtime/src/py_obj.c
51642fc14cd4accac1860cc14a664967b6d7b93624b43c7c102e0a4e1f61b10f  pcc/py_runtime/src/py_gc_backend.c
c9c5d19468b25039d4fa47fccc102b7bab069ae66a7019dfe1e0b5039d1d5eed  pcc/py_runtime/py/py_obj.py
46afe278fa44dac2f9e69ecbd1e883694b075094ac24166bb9de2bd0989d07e2  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
fff752c5ed19234568d00f461e36d590884d4368d56eb43fb2b49e37f83ee252  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
da8aac66823e9ed461942950f62a8ec05de7830cb47c66045feb53efaf9a2113  pcc/py_runtime/py/py_gc_backend.py
bc12826c10337c3a125f2d2f4a4ea21d453cd565c6431dcddde65db2fb76c5ce  pcc/py_frontend/codegen/runtime_abi.py
6e71eec03f97ae88a3eccc693d5bc890e6278c2c7b03af0428d59e975226c0bf  tests/python/test_freestanding_gc_relocation_copy.py
092b6972e61bd60dd643f8ebb89c5eec585ce519a0db259f02872646a85b7f6a  tests/python/test_freestanding_gc_relocation_payload.py
12d136cacfcf1a91b3f2dfba8cc7cd8815c5cf40d154ac4c853154575b89e1f3  tests/python/test_gc_backend4_production.py
```

Whole-slice `git diff --check`, isolated Python compilation, and threaded plus
threads-off C syntax checks are green.  Both C syntax modes emit the same ten
pre-existing warnings and no new warning.

## RED and correction evidence

The first source-contract test was added before implementation.  It failed in
0.10 seconds because the old strict two-argument unlocked export remained and
the public copy performed no external slot-plan preparation.

The first compiled strict owned-slot test then exposed a real mirror defect:
the destination slot visitor read the strict global callback context, but the
new validation phase had already cleared that context.  The copy returned
`NULL` (probe return 7), producing `1 failed in 124.07s`.  The fix publishes
the plan context immediately around the destination visit and clears it on
every return.  The corrected node passed in 122.85 seconds.

A subsequent local source pass found that strict allocated the structural
finish plan but did not initialize it when validation failed before commit.
Both pointer fields are now cleared before the commit lock, and the exact
source-order gate pins initialization before validation and every later read.

One archive-owner attempt used the wrong fixture route and reached its 90
second watchdog without a final pytest summary.  It is diagnostic non-evidence;
the residue check found no surviving pytest, compiler, make or probe process.
The final owner packet explicitly reused the verified archive below.

## Final focused gates

The final current-source strict node produced a durable summary:

```text
test_backend4_strict_relocation_copy_balances_owned_slot_retain
1 passed in 122.71s
```

Log: `build/gc4-a3b-payload-slot-retain-strict-final.log`, SHA-256
`4f73d7a1489df428c9499caf7a98f61cefd171592c06c8b3e3fb4f772d08977c`.

The exact C runtime packet covers the owned-slot retain, DEALLOCATING
quarantine, container payload stress, remembered-list retarget and inline-tuple
retarget: `5 passed in 0.88s`.  The C and strict GC3 oldification neighbors pass
`2 passed in 0.86s`.

Strict copy/payload source ownership plus LLVM/self closure and exact transaction
contracts pass `13 passed in 4.10s`.  Explicit archive copy owner, payload owner
and strict quarantine pass `3 passed in 0.85s`.  Refcount/public-wrapper/root
transaction source neighbors pass `3 passed in 0.08s`; continuation and
relocated payload-span source neighbors pass `2 passed in 0.08s`; forwarding
retirement source/closure neighbors pass `7 passed in 2.59s`.

The dynamic owned-slot probes assert a list child's exact count changes from
one source ownership to two source-plus-target ownerships, the target slot is
published only after the real retain, and the moved object has the exact three
counts from its allocation result, forwarding edge and transferred source
count.

No broad default suite, stage/bootstrap chain, performance profile or five-GC
matrix was run for this focused slice.

## Strict archive receipt

The final cache key is `b62843ca5db383f963a79ee3-threaded-pcc-py`.  The
production provenance verifier passed against the frozen current
`pcc/py_runtime` source root.

```text
8230e32fe21d397e0a4e006161c12973960506f795f445d26e404398dda58d8e  libpy_runtime_pcc_py.a
5f1b63fc05254ef4d7c8fa4d3c19b58d513ec0aca49d64e2db2bd007213dba72  libpy_runtime_pcc_py.a.provenance.json
71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda  libpy_runtime_pcc_py.a.capi_syms
1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1  libpy_runtime_pcc_py.a.target
82656f494aafc8c337cbe5cf3915ce3485263447f2529620a5bac12f90700138  .pcc-threaded-pcc-py-complete
```

The marker is schema `pcc.runtime-build-cache.v4` with the exact key and
receipt hashes.  The provenance is schema
`pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target
`arm64-apple-darwin25.5.0`, 186 pcc-Python members and 444 C-API symbols.  All
members use producer kind `pcc-python-library-ir-to-obj`, the llvmlite target
machine object emitter, and no host C compiler.

## Review and boundaries

Following the user's request to minimize agent use, the final identity was
reviewed locally.  No sub-agent was started or contacted and no independent
sub-agent verdict is claimed.

This slice does not make the complete payload commit a bounded graph-lock
leaf.  Type-specific list/dict/class/continuation raw-buffer allocation,
copy/rollback and ZPage owner-span registration remain inside the commit.
Forwarding/identity-index/ZPage commit, final remap and retirement, raw mutator
payload quiescence and target-death cleanup also remain open.  The private
five-argument strict preallocated helper still relies on its internal caller's
graph-lock precondition; invalid/debug diagnostic parity is not claimed.

GC3's compatibility payload wrapper still prepares and finishes retain tokens
inside its existing generational graph holder.  Nested outer-lock callers,
concurrent drains or backend switching, selected-source/page lifetime and
destroy/reuse epoch/ABA, remembered-root admission, refmeta and
`BIASED`/`DEFERRED`, callback roots, C-API raw views, resurrection restoration,
physical movement, A3c no-park integration, stage/performance, fixed point and
broad five-GC parity remain unproved.  The parent task remains `IN_PROGRESS`.
