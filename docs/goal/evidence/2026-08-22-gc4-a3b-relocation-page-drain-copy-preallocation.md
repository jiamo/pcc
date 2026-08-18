# GC4 A3b relocation page-drain and copy preallocation

Date: 2026-08-22

## Claim

For one stable Backend 4 selection, valid managed values, threads enabled and
the default `ATOMIC` refcount strategy, the outermost public relocation-page
drain and public relocation-copy paths in the C transition oracle and strict
freestanding pcc-Python runtime no longer retain their own graph-lock scope
while allocating a destination, releasing detached queue nodes, dropping a
failed/returned destination reference, or polling a safepoint.

The page drain snapshots at most 16 selected sources while graph-locked, then
unlocks before calling the public copy path, dropping the returned target and
polling.  The public copy path snapshots source eligibility under the graph
lock, unlocks before destination allocation, re-locks for the serialized copy
commit, and unlocks before structural-node cleanup and failure decref.

The commit returns detached relocation-set and evacuation-page nodes through a
two-pointer finish plan.  Its C layout is exactly 16 bytes with fields at
offsets 0 and 8; the strict caller allocates the same 16-byte record.  The
strict commit directly owns its freestanding list/ZPage mutations instead of
calling the four managed relocation/page helpers that previously hid provider
work in the locked path.  Its failure paths no longer decref the destination;
the public caller performs that cleanup exactly once after unlock.

This is an **outermost helper-own lock-scope** claim.  It is not a claim that
the locked copy commit is a bounded leaf: payload copying, forwarding install,
known-object/index work and ZPage removal remain inside it.  The final remap
and retirement call also remains graph-locked.

## Frozen source identity

```text
35f33fb3061de3d44550aaa12972972dc7bae3feb60bfe1eef30c5be178e8442  pcc/py_runtime/src/py_gc_backend.c
4ac9b1851e9a2a49d05d62ded41b9b0dcafb941fb721016a4f815367d720cf0e  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
61682e82a8c35d9c20f54bfad90cc216715086fb7aa090ea3dc7f32df50cb30d  pcc/py_runtime/py/freestanding_gc_relocation_drain.py
092139b685f36401e311cf479f875030d1c7eea265eaef589cd90e4d05b9a9fc  pcc/py_frontend/codegen/runtime_abi.py
62dea98741992e00dcd9ddd0b62f9089ff79e0289412a3b7fa426e9befd4b68c  tests/python/test_freestanding_gc_relocation_copy.py
0404ce57d196fa34d8f67f529f41af8d0effb11713b01cbf2a534432e1ba2604  tests/python/test_freestanding_gc_relocation_drain.py
6f22e9b5515cbc9efdceaaf6c985a8572d49a37d34a2298fd12cf99f6c99174a  tests/python/test_gc_backend4_production.py
5d4bb91f45db165fc0ef2d9adf913fab9598be48318f96a6e2533497856f00ca  tests/python/test_gc_threading_substrate.py
```

Whole-tree `git diff --check` and isolated Python compilation are green on
this identity.  Threaded and threads-off C syntax checks both pass with the
same ten pre-existing warnings and no new warning.

## RED and correction history

The original C page drain reached destination allocation while retaining the
graph lock.  The deterministic true-pthread probe published a real stop-the-
world request, let the drain worker enter public page drain, and then had the
stopped-world owner acquire the same public graph lock.  The worker parked at
the allocation safepoint while still holding that lock, so the subprocess
watchdog expired:

```text
test_colored_relocation_page_drain_polls_only_after_releasing_graph_lock[c]
1 failed in 10.81s
```

The first partial C split still failed the same focused node in 16.73 seconds;
that intermediate result is not reported as green evidence.  The final path
snapshots under the lock and performs the public allocation/copy/tail sequence
after releasing the snapshot tenure.

Strict bring-up exposed link/ABI and watchdog failures before the current
four-argument preallocated helper and finish-plan layout were complete.  Those
intermediate runs were implementation diagnostics, not passing evidence.

The final solo review found a real strict cleanup defect: payload or forwarding
failure inside the preallocated commit decrefed `to_obj` while graph-locked,
and the public caller then decrefed it again.  The commit no longer decrefs;
the static contract requires a single failure cleanup after public unlock.

Two later 60-second commands entered a cold archive build and ended without a
final pytest summary.  They were checked for residue and are deliberately not
counted as green evidence.  The final cold strict node and archive neighbors
below have complete summaries.

## Final focused gates

The exact strict source-owner, LLVM/self closure and transaction/budget-order
packet for relocation copy and drain passed on the final identity:

```text
8 passed in 2.00s
```

The compiled true-pthread page-drain handshake passed in both runtime modes:

```text
C + pcc-Python: 2 passed in 0.68s
```

The probe selects 32 objects across two pages.  After the drain worker observes
the real stop request, the stopped-world owner acquires the public graph lock
at the first destination-allocation safepoint and still observes all 32
candidates.  After resume, the worker returns 32, the relocation set is empty,
and forwarding telemetry is exactly 32.  The handshake uses no sleep or yield.

The final current-source strict cold node produced a durable summary:

```text
test_colored_relocation_page_drain_polls_only_after_releasing_graph_lock[pcc_python]
1 passed in 123.23s
```

Its log is `build/gc4-a3b-relocation-page-drain-strict-final4.log`, SHA-256
`3e4f18133e9e513c77945e8f3a5048f6f0eea5f651dedfa8e6fb67fb09e1e638`.

Strict archive ownership plus object/page/step C-oracle differential neighbors
passed 5 nodes in 129.86 seconds.  Durable log
`build/gc4-a3b-relocation-page-drain-archive-neighbors.log` has SHA-256
`0e81cee5745bf69ca4639cc0c5cafc8e29bd70817d5a9074b53233ba00eae309`.

Eight exact C page-handoff, whole-page, retirement-span and telemetry neighbors
passed in 0.61 seconds.  Durable log
`build/gc4-a3b-relocation-page-drain-c-neighbors.log` has SHA-256
`c8d78f2829abe9c62ddcb13b7059f8ab5e857c4af404bfcddc53ba411c307209`.

Seven focused selector neighbors passed in 1.70 seconds.  The exact
DEALLOCATING quarantine gates passed independently in static, C and strict
modes (`0.28s`, `0.18s`, `0.57s`), and the public telemetry wiring node passed
in 0.28 seconds.

No broad suite, stage/bootstrap chain, performance profile or five-GC matrix
was run for this slice.

## Strict archive receipt

The final archive key is
`723259040d68108ef9f39666-threaded-pcc-py`.  The production provenance
verifier passed against the current `pcc/py_runtime` source root.

```text
739761dd637d9b7f43b86af9375ed473423ce551b5dc9c19c72c82887af20eb7  libpy_runtime_pcc_py.a
0b060c259b077fcdabc61a2b3150e3fe4448f1cda4f6dfbd78925f0a9c8a6e3e  libpy_runtime_pcc_py.a.provenance.json
71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda  libpy_runtime_pcc_py.a.capi_syms
1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1  libpy_runtime_pcc_py.a.target
ab0e44e244cc021e4e17e82db05eb4b30ac5a80604c234eac2a0a120a3b89602  .pcc-threaded-pcc-py-complete
```

The manifest reports schema `pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target
`arm64-apple-darwin25.5.0`, 186 pcc-Python members and 444 C-API symbols.  The
completion marker is schema `pcc.runtime-build-cache.v4` and matches the
archive, manifest, C-API inventory and target hashes.

## Review and boundaries

Following the user's request to minimize agent use, the final page-drain/copy
identity, source shape, tests, archive receipts and claim wording were reviewed
locally in two adversarial passes.  No independent sub-agent verdict is
claimed.

This closes destination allocation, detached-node cleanup, returned/failed
destination decref and safepoint placement for the stated outermost public
page-drain/copy path.  It does **not** close nested callers that already hold an
outer graph lock, concurrent drains/collectors, selected-source/page lifetime
or destroy/reuse epoch/ABA, and it does not prove stale/interleaved candidate
fairness beyond the bounded first-16 snapshot.

The legacy object drain still uses the unsafe unlocked copy helper while
graph-locked.  Copy-payload transfer, forwarding install, identity/index and
ZPage removal, final remap/retirement, remembered-root owner/referent bounds
and phase admission, GC3 holders, invalid-state tripwires/logging, refmeta,
`BIASED`/`DEFERRED`, concurrent/unstable backend switching, raw mutator
payload quiescence, callback roots, resurrection restoration, physical
movement, A3c no-park integration, stage/performance, fixed point and broad
five-GC parity remain unproved.  The parent task remains `IN_PROGRESS`.
