# GC4 A3b relocation-selection bounded scans

Date: 2026-08-22

## Claim

For one stable Backend 4 selection, valid managed values, threads enabled and
the default `ATOMIC` refcount strategy, the C transition oracle and strict
freestanding pcc-Python runtime now bound both remaining relocation-selector
membership scans to at most 16 examined entries per graph-lock tenure.

The global selector keeps its candidate cursor and best candidate in persistent
runtime state.  It examines no more than `PCC_GC_SAFEPOINT_BATCH` ZPage nodes,
unlocks, polls, and reloads the authoritative cursor before the next tenure.
The page selector applies the same rule to the page-local `page_next` object
chain.  Cursor and seed state are advanced or invalidated before an object or
ZPage node can be unlinked and recycled.  Selection plans are still allocated
outside the graph lock and committed through the already-proven bounded
transaction.

Candidate scoring no longer hides an unbounded object-list walk.  It reads the
candidate ZPage node's `size_bytes` directly.  Remembered-slot pressure is also
O(1): `PccGcZPageNode` now carries a per-owner count at offset 72, grows from 72
to 80 bytes, and is updated by the common add/remove/clear/retarget path.  The
strict runtime mirrors the same layout and counter semantics.  These changes
preserve the prior page-policy preference for dirty owners without scanning the
global remembered-slot list while graph-locked.

This is a bounded *examined-iteration* claim, not a formal wait-free claim.
The page-local seed may be encountered once as the priority seed and later
skipped in the cursor chain; both encounters consume the 16-entry allowance,
while scoring and admission still occur at most once.

## Frozen source identity

```text
ab33b63d0319e82bd33f19649bdad57e8b99b2538e38873aaa4e3c54a3e59d8b  pcc/py_runtime/src/py_gc_backend.c
2e77dea30ba56d1bf05fb3870e8fc9291fee715eb2b31f260d24997717c9ae73  pcc/py_runtime/py/freestanding_gc_relocation_selector.py
038a3570d5e65f71dc086eecd80b554d230fa4db4418e6883bd2255725fa8312  pcc/py_runtime/py/freestanding_gc_state.py
8447456a5f35dc7abb4eb1c781bf54336847119afd01ab87baf5ef94df4db67c  pcc/py_runtime/py/freestanding_gc_zpage_allocation.py
1ca97b5ff213fbb82b9f7abe147dcc97ae2f9274962433e5a04dc0e911c6a191  pcc/py_runtime/py/freestanding_gc_zpage_lifecycle.py
4180802d3a42fae7dc6795bb30488f4534fc4fd8071fc03f7e8fea7a1af5cfff  pcc/py_runtime/py/freestanding_gc_zpage_mechanics.py
83a6cd03373575cb8118d1a60692de43fb5d975aa7069b9d5fb14d94c3bc6b67  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
8ce2d0483f594e5ee9e7d987f196376e41696a2867305305bff4d478ad9d2f2d  pcc/py_runtime/py/py_gc_backend.py
1257bde6f9f5f068937bedc7a4e729a078bffaef5540cf010e4bc8f5ba5ada3b  pcc/py_frontend/codegen/runtime_abi.py
c50380835335c0a09495b4b576794ab3f5ece1b56b39b318cc25f88723eb722f  tests/python/test_gc_threading_substrate.py
a8af714ec2ef372b0ad2d784e425bb5fc2d41f062654dde13c43cfa10ff64e4a  tests/python/test_freestanding_gc_relocation_selector.py
535c0f7228e43731b7309ff8510db8df2dd1e0383ae56e3b7e47df3240576df9  tests/python/test_freestanding_gc_zpage_mechanics.py
40456c31f5d59a2ea8ca38a1b4f9221d316f7734b9c25a54d313ac77e4fe8b0a  tests/python/test_gc_backend4_production.py
```

Whole-tree diff hygiene, isolated Python compilation, and both threaded and
threads-off C syntax checks are green on this identity.  The C checks report
the same ten pre-existing warnings in both modes and no new warning.

## RED and correction history

The predecessor transaction evidence already records the original direct
graph-lock-to-safepoint failures: the C global selector timed out in 10.45
seconds, the strict selector timed out in 11.02 seconds, and the old C page
selector committed 32 objects in one tenure and failed in 6.66 seconds.  This
slice did not relabel those historical results as new failures.

After allocation and polling had been split from commit, the new bounded-
discovery probes exposed the remaining issue: at the stopped-world midpoint,
both global and page selectors had already selected 16 candidates where the
new contract requires a discovery chunk to unlock and poll before committing
selection.  The exact intermediate timings were not retained, so this evidence
records the causal product RED without inventing a duration.

Solo source review found a second hidden unbounded edge: the global scorer used
`pcc_gc_known_object_size_unlocked`, whose fallback can scan the full object
list.  It now reads `zp->size_bytes` directly.  The first spelling used
`zp->size` and failed C compilation; that was an implementation correction,
not runtime semantic evidence.

Removing the global remembered-slot scan initially changed policy semantics.
The exact existing neighbor
`test_backend4_genzgc_selector_uses_zpage_remembered_pressure` selected the
clean owner instead of the dirty owner and failed with probe return code 6.
Adding the per-owner O(1) counter restored the policy; the exact rerun passed:

```text
1 passed in 0.17s
```

One source gate initially sliced a forward declaration rather than the function
definition and produced `1 failed, 1 passed in 0.29s`.  Changing that test-only
slice to the definition made the exact static node pass in 0.25 seconds.  This
was a harness correction and did not change production.

## Final focused gates

The final C true-pthread global/page selector pair passed:

```text
2 passed in 7.03s
```

The strict pair ran against a fresh provenance-verified threaded pcc-Python
archive with visible node IDs and a durable log:

```text
test_colored_relocation_selector_polls_only_after_releasing_graph_lock[pcc_python]
test_colored_relocation_page_selector_polls_only_after_releasing_graph_lock[pcc_python]
2 passed in 123.58s
```

The log is `build/gc4-a3b-relocation-selector-bounded-strict.log`, SHA-256
`29da2b73223742c2ef68bedfd903284e469aa8e404896563c970cc01e6beef78`.

The O(1) remembered-pressure integration passed independently in both modes:

```text
C:            1 passed in 0.15s
pcc-Python:   1 passed in 0.54s
```

Six exact remembered add/remove/retarget/telemetry neighbors passed in 0.52
seconds.  Twelve selector/page policy neighbors passed in 0.95 seconds.  The
final exact static/runtime cross-check packet passed 9 nodes in 1.72 seconds.

Strict ZPage ownership, LLVM/self closure and 80-byte layout checks passed 4
nodes in 1.49 seconds.  The two archive-owned ZPage state-machine nodes passed
in 124.52 seconds; durable log
`build/gc4-a3b-zpage-mechanics-archive.log` has SHA-256
`29b80dbfda56695c57a4cc86882fe3c75fc5fc540f11bbd0d24fdcd2d37e9b04`.
Strict selector source ownership, LLVM/self closure and policy checks passed 4
nodes in 1.63 seconds, and its production-archive owner node passed in 0.72
seconds.

No broad suite, stage/bootstrap chain, performance profile or five-GC matrix
was run for this slice.

## Strict archive receipt

The final archive key is
`f80aabb25243be8c44435fdb-threaded-pcc-py`.  The production provenance
verifier passed against the current `pcc/py_runtime` source root.

```text
1a0943829255b336169a7349f03d047ddfed57b0616ccfa57b413403a7565c38  libpy_runtime_pcc_py.a
ad3a8855dc3600d4d3ac1b710775178e8caee676b1e9f032db31e0a25cb0c4a7  libpy_runtime_pcc_py.a.provenance.json
71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda  libpy_runtime_pcc_py.a.capi_syms
1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1  libpy_runtime_pcc_py.a.target
b8c7ebbe3aca5a6e7e8d73ed186d3b827b5d6b49a5ce19e0a8aa6f6b1b84e188  .pcc-threaded-pcc-py-complete
```

The manifest reports schema `pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target
`arm64-apple-darwin25.5.0`, 186 pcc-Python members and 444 C-API symbols.
Every member reports `uses_host_cc=false`; the selector receipt matches the
frozen strict source hash.  The completion marker is schema
`pcc.runtime-build-cache.v4` and matches the archive, manifest, C-API inventory
and target hashes.

## Review and boundaries

Following the user's request to minimize agent use, the final identity, source
shape, tests, archive receipts and claim wording were reviewed locally.  No
independent sub-agent verdict is claimed.

This closes the previously open global-candidate and page-local membership
scan bounds for the stated stable Backend 4 path.  It does **not** close page
destroy/reuse epoch or ABA safety, starvation, constructor-publication races,
formal atomic/index wait-freedom, concurrent backend switching, or nested
outer-lock callers.

Relocation drain/copy/remap/retirement holders, remembered-root
owner/referent bounds and enqueue-to-phase-admission races, GC3 graph-lock
holders, invalid-state tripwires/logging, refmeta, `BIASED`/`DEFERRED`, callback
roots, resurrection restoration, physical relocation, A3c no-park
integration, stage/performance, fixed point and broad five-GC parity remain
unproved.  The parent task remains `IN_PROGRESS`.
