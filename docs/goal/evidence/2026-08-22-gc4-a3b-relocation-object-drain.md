# GC4 A3b relocation object-drain tail

Date: 2026-08-22

## Claim

For one stable Backend 4 selection, valid managed values, threads enabled and
the default `ATOMIC` refcount strategy, the outermost public relocation object
drain in the C transition oracle and strict freestanding pcc-Python runtime no
longer retains its own graph-lock scope while sizing/copying a source, dropping
the returned target reference, or polling a safepoint.

Each drain tenure snapshots at most `min(remaining_budget, 16)` source pointers
from the authoritative relocation-set head while graph-locked.  It then unlocks
before calling the public relocation-copy API, decrefing its returned target
and polling.  The next tenure reloads the authoritative head rather than
carrying a relocation-node cursor across unlock.  Incomplete-batch telemetry
and the existing remap-if-drained handoff remain serialized by one final short
graph-lock tenure.

The C-only private `pcc_gc_relocate_copy_unlocked` implementation became dead
after this split and was removed instead of being retained as an unsafe
wrapper.  The strict internal ABI with that historical name remains exported
for compatibility but is not called by the object drain.

This is an **outermost helper-own lock-scope** claim.  It does not make the
public copy commit or final remap/retirement a bounded graph-lock leaf.

## Frozen source identity

```text
c3257d0a58caf93c1a707643f4ca67160cceac539660caca71bfe818a7de55b7  pcc/py_runtime/src/py_gc_backend.c
89dd6aef43616c537c9b5cbbff59b42cf27adc35d14fad16b0973771cf6a7e2e  pcc/py_runtime/py/freestanding_gc_relocation_drain.py
792e898365bb2e2306e3fe05913ec4e6e5c2abe6075c6d4e8e1e8fd7a0375d6c  tests/python/test_gc_threading_substrate.py
9e551897e816f073e828874110c5e7e957916add0c77f25b4ed33133f7c7a296  tests/python/test_freestanding_gc_relocation_drain.py
c242972f31df0618fa58ad023d86120a8e13117491c6167a6874150b64a93900  tests/python/test_freestanding_gc_relocation_copy.py
7ab35baa6905c6e2ba205ee17baadebfa723b27ed981febeeecc69ff40439ccc  tests/python/test_gc_backend4_production.py
```

Whole-tree `git diff --check` and isolated Python compilation are green on
this identity.  Threaded and threads-off C syntax checks both pass with the
same ten pre-existing warnings and no new warning.  Removing the now-unused C
copy helper eliminated the one new warning exposed by the first syntax pass.

## Genuine RED evidence

The old C object drain held the graph lock around its complete relocation-set
walk and called destination allocation, returned-target decref and safepoint
from that locked region.  The new public-interface tracer was added before the
production change and run alone:

```text
test_colored_relocation_object_drain_polls_only_after_releasing_graph_lock[c]
1 failed in 10.47s
```

The drain worker observed a real stop request and entered
`pcc_gc_backend4_evacuation_drain(32)`.  It parked at the first destination-
allocation safepoint while retaining the graph lock.  The stopped-world owner
then attempted the same public graph lock, so the probe watchdog expired after
10 seconds.  The handshake uses no sleep or yield.

After the C snapshot/unlock split, the same exact node passed in 6.68 seconds.
The strict source-shape gate was then strengthened before its mirror change and
failed in 0.09 seconds because `_relocate_selected` had no bounded snapshot
buffer and carried `nxt` across public copy calls.  The exact gate passed in
0.06 seconds after the strict mirror landed.

## Final focused gates

The final compiled true-pthread object/page drain packet passed in both runtime
modes on the frozen identity:

```text
4 passed in 1.26s
```

For the object-drain case, the stopped-world owner acquires the public graph
lock at the first destination-allocation safepoint and still observes all 32
candidates.  After resume the worker returns 32, the relocation set is empty,
and forwarding telemetry is exactly 32.

The final current-source strict cold node produced a durable summary:

```text
test_colored_relocation_object_drain_polls_only_after_releasing_graph_lock[pcc_python]
1 passed in 123.40s
```

Its log is `build/gc4-a3b-relocation-object-drain-strict.log`, SHA-256
`dbdcd371f86c690666cb7ea61167e8039358304da93afb49225d802e81d7ac3a`.

Strict source ownership, LLVM/self closure and exact object/page lock-order
contracts passed 4 nodes in 1.16 seconds.  Archive ownership plus object/page/
step C-oracle differential checks passed 5 nodes in 1.94 seconds.  Durable log
`build/gc4-a3b-relocation-object-drain-archive-neighbors.log` has SHA-256
`efea2ccdc6f738f17b02033155452bb7efdd7f41c14406ca39bc06ad12715ee5`.

Eight exact C incomplete-batch, object/page handoff, whole-page, retirement-span
and telemetry neighbors passed in 0.59 seconds.  Static, C and strict
DEALLOCATING quarantine gates passed 3 nodes in 0.59 seconds.

No broad suite, stage/bootstrap chain, performance profile or five-GC matrix
was run for this slice.

## Strict archive receipt

The final archive key is
`7ea42b62a5f31097975efb9b-threaded-pcc-py`.  The production provenance
verifier passed against the current `pcc/py_runtime` source root.

```text
0819c72bab14113bfe49371a0c4fd534d1279c5e7b5c1342f0fbcad6ba9f2645  libpy_runtime_pcc_py.a
94f677ae0732f36910d359caa3736cb3095d6ea14097d721fa9c73dd3e3b25c7  libpy_runtime_pcc_py.a.provenance.json
71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda  libpy_runtime_pcc_py.a.capi_syms
1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1  libpy_runtime_pcc_py.a.target
769697ed559a63c066642d4b271cd32a6994c03ef755dd0d2f1125946c5cfd15  .pcc-threaded-pcc-py-complete
```

The manifest reports schema `pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target
`arm64-apple-darwin25.5.0`, 186 pcc-Python members and 444 C-API symbols.  The
completion marker is schema `pcc.runtime-build-cache.v4` and matches the
archive, manifest, C-API inventory and target hashes.

## Review and boundaries

Following the user's request to minimize agent use, this identity was reviewed
locally in two adversarial passes.  No sub-agent was started or contacted and
no independent sub-agent verdict is claimed.

The review pins the bounded snapshot, head reload, unlock-before-public-copy /
decref/poll order, final telemetry/remap lock region and absence of the removed
C unlocked helper from the active drain.  It does **not** prove nested callers
that already retain an outer graph lock, concurrent drain/collector calls,
selected-source lifetime or page destroy/reuse epoch/ABA, or progress/fairness
past a prefix of candidates that all fail revalidation/allocation.

Copy-payload allocation and ownership work, forwarding install, identity/index
and ZPage commit work, the still-callable strict internal unlocked-copy ABI,
final remap/retirement, remembered-root owner/referent bounds and phase
admission, GC3 holders, invalid-state tripwires/logging, refmeta,
`BIASED`/`DEFERRED`, concurrent/unstable backend switching, raw mutator payload
quiescence, callback roots, resurrection restoration, physical movement, A3c
no-park integration, stage/performance, fixed point and broad five-GC parity
remain unproved.  The parent task remains `IN_PROGRESS`.
