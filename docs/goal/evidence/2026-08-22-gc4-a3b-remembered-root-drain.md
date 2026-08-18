# GC4 A3b remembered-root drain tail

Date: 2026-08-22

## Claim

For one stable Backend 4 selection, valid managed values, an outermost public
GC step, threads enabled and the default `ATOMIC` refcount strategy, the C
transition oracle and strict freestanding pcc-Python runtime now drain one
remembered-root/store-buffer batch without running callback-capable cleanup
under the object-graph lock.

The step moves at most one medium-buffer capacity into the global list and
drains at most eight entries.  Entry detach, exact maintenance-work counting,
valid `YOUNG -> OLD` promotion, slot/root rewriting, owner `REMEMBERED` state
and structural telemetry remain serialized by the graph lock.  C allocates
any global-list nodes before its outermost locked transaction.  Both runtimes
release the graph lock before freeing detached nodes, dropping saved buffer
references, running finalizers and polling a safepoint.  The public work result
counts every detached entry, including stale/no-longer-remembered maintenance
work, rather than only successful promotions.

The strict owner of `pcc_gc_backend4_step_remembered_roots` moved from ordinary
`py_gc_backend.py` into the freestanding generational scheduler without a
symbol or C-ABI change.  This prevents compiler-injected entry/backedge polls
inside its locked transaction.  When the dispatcher observes that remembered
entries remain after a batch, it does not enter the later GC4 aging,
evacuation, selection, tracing, remap or retirement chain in that step.

The valid GC4 shared promotion helper now uses the same adjacent-bit atomic
add in C and strict pcc-Python: adding `YOUNG` clears `YOUNG`, carries into
`OLD`, and preserves concurrently published unrelated header flags.  The C
maximum-batch CAS telemetry update also runs only after graph unlock.

## Frozen source identity

```text
19d3c32c018bc6cffd3c5ef801d10a254716854d5a9e0358211f43c536d12ba2  pcc/py_runtime/src/py_gc_backend.c
34413b6403b35ec3d2096d02f3740ce0192af1006fe793b7042333e30dde0ca3  pcc/py_runtime/py/py_gc_backend.py
aef51ffd47f039790be8f07ca28d3a3c9151905d5110231cdf33756a09e8277e  pcc/py_runtime/py/freestanding_gc_generational_scheduler.py
e123913e89155da9e694e5de8bdec18b833ac5364a48c1e00a9c5858c33d7894  pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py
8e4ce4d0ab85978c8dd10d87d6830bf62c66738c2b81c968daa74455603972e4  pcc/py_runtime/py/freestanding_gc_generational_promotion.py
963f5adeca1378b9804c05a3483f533a88a4290ab60aaf5d82793e4e3111fca3  tests/python/test_gc_threading_substrate.py
8c6423acc6dc804da6735cffe27f44967d63dc11de5c4e2180c68909327953b9  tests/python/test_freestanding_gc_generational_scheduler.py
f429f61a30df5f51cac9bb03d97eceada59decc1b93f82b3e645e203dead1b8d  tests/python/test_freestanding_gc_barrier_dispatcher.py
d281b7ca72b2b6c9974c93c4075107f20a938f76c9e9755df6ad79dda910f522  tests/python/test_freestanding_gc_generational_promotion.py
```

`git diff --check` is clean.  Isolated `py_compile` passed for every changed
runtime/test Python file.  C `-fsyntax-only -Wall -Wextra` passed with
`PCC_WITH_THREADS=1` and `0`; both modes reported the same 10 pre-existing
warnings and no new warning.

## Genuine RED evidence

The old remembered-root drain dropped the buffer's retained reference while
holding the graph lock.  The deterministic true-pthread probe used:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_threading_substrate.py::test_colored_remembered_root_finalizer_runs_after_graph_unlock
```

The buffer retain was the terminal reference.  Its finalizer released a
worker whose next operation called public `pcc_gc_object_is_known(anchor)` and
then joined that worker.  Old code formed a finalizer-join/graph-lock cycle;
the child watchdog expired after 10 seconds and pytest reported
`1 failed in 10.49s`.  The handshake contains no sleep or yield.

The first transaction-shape gate failed in 0.26 seconds because the C body had
no fixed saved-entry batch and performed the cleanup inline.  Moving the
strict symbol next produced a source-owner RED (`1 failed in 0.08s`) until the
owner/undefined-symbol closure was updated.  One closure failure from a
trailing comma in a multiline test extern was a harness correction, not
production evidence.

The maintenance-work probe then exposed a phase-order bug.  With ten queued
entries and a public budget of ten, the first implementation drained eight
but continued into later GC4 work and returned:

```text
first=19 entries=2 batches=1 drained=8
```

The final dispatcher blocks the entire later GC4 phase chain whenever its
post-drain check observes pending store-buffer entries.  The first call now
returns eight, leaves two entries, and the second call returns two even after
the owner is no longer `REMEMBERED`.

Finally, the strict promotion source test failed before implementation because
no backend-4 atomic transition branch existed (`1 failed in 0.25s`).  The final
C/strict branches both use an acquire-release atomic add on the valid
`YOUNG && !OLD` transition.  A subsequent assertion-slice failure was a test
harness correction (the unlink occurs immediately before the backend-4
branch), not a product RED.

## Final focused gates

Source ownership/closure, the remembered transaction contract and both C
runtime paths passed together:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -q -x -n0 --tb=short \
  tests/python/test_freestanding_gc_generational_promotion.py::test_generational_promotion_has_one_strict_source_owner \
  tests/python/test_freestanding_gc_generational_promotion.py::test_generational_promotion_has_exact_strict_object_closure \
  tests/python/test_freestanding_gc_generational_promotion.py::test_generational_promotion_preserves_owned_borrowed_and_stable_root_contracts \
  tests/python/test_gc_threading_substrate.py::test_colored_remembered_root_drain_defers_blocking_tail_and_medium_flush \
  'tests/python/test_gc_threading_substrate.py::test_colored_remembered_root_finalizer_runs_after_graph_unlock[c]' \
  'tests/python/test_gc_threading_substrate.py::test_colored_remembered_root_drain_counts_maintenance_work[c]'
```

Result: `7 passed in 8.49s`.

The final strict threaded archive and both strict runtime paths used live node
IDs and a durable log:

```bash
gtimeout 600s zsh -o pipefail -c 'gtimeout 540s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short "tests/python/test_gc_threading_substrate.py::test_colored_remembered_root_finalizer_runs_after_graph_unlock[pcc_python]" "tests/python/test_gc_threading_substrate.py::test_colored_remembered_root_drain_counts_maintenance_work[pcc_python]" 2>&1 | tee build/gc4-a3b-remembered-root-strict-atomic-final.log'
```

Result: `2 passed in 124.21s`; log SHA-256
`a3af05cc4d2234045350353d210d101a97443c65a06109b49e2808e2418f87ce`.

Against that explicit archive, the promotion/scheduler/dispatcher unique-owner
tests plus C/strict old-to-young barrier behavior passed `5 in 8.53s`.  The
seven store-buffer neighbors and seven C/strict generation-aging neighbors
passed `14 in 2.46s`.  On the final test identity, the static contract plus
C/strict finalizer and maintenance nodes passed `5 in 1.43s`; the strengthened
C/strict atomic and GC4-before-allocation source gates passed `2 in 0.26s`.

Two earlier full promotion-test invocations used a 60-second watchdog and
reached four dots while the last default archive fixture was still cold.  Both
ended without a final pytest summary and are not green evidence.  Residue
checks found no surviving pytest, compiler, make or probe process.  The final
threaded archive above was built once and then reused for every archive-owner
and strict neighbor gate.

## Strict archive receipt

The final archive key is
`616546c975d7ee43506d4467-threaded-pcc-py`.  The production provenance verifier
completed successfully against the current `pcc/py_runtime` source root.

```text
2951e761acd93d3632d3f59e9a2942b125809e2f8d3e1b58c9b9f309d188d37b  libpy_runtime_pcc_py.a
dd9960d0272f0a410d97d53f7a5160fad36557a267bffc31489adb2f41e84151  libpy_runtime_pcc_py.a.provenance.json
71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda  libpy_runtime_pcc_py.a.capi_syms
1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1  libpy_runtime_pcc_py.a.target
49c1e0df3f1b1e2c44299c7181fca8ceaec6c4030fde19b6a5c4e2e00168fa86  .pcc-threaded-pcc-py-complete
```

The manifest reports schema `pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target
`arm64-apple-darwin25.5.0`, 186 provenance members and 444 C-API symbols.  All
members report pcc-Python provenance and `uses_host_cc=false`.  The completion
marker is schema `pcc.runtime-build-cache.v4` and exactly matches the archive,
manifest, C-API inventory and target hashes above.

## Review and boundaries

The tracer and initial transaction implementation predate the user's request
to reduce sub-agent usage.  After that request, the final identity was
converged and reviewed locally in two explicit passes: production
lock/ownership/phase ordering, then test/owner-closure false-green analysis.
No sub-agent participated in the final changes or review, so this evidence
does not claim an independent dual-review verdict.

This closes the direct remembered-root drain edges to allocation/free,
last-decref/finalizer and safepoint work for the stated outermost valid path.
It does **not** prove a fully bounded remembered-root leaf: owner-pending and
referent traversal remain potentially unbounded, the enqueue-side medium flush
is still part of the broader `store_ptr` holder, and the low-level atomic RMW
does not carry a formal wait-free proof.  An enqueue racing between the
post-drain count check and the next GC4 phase acquisition is also not an atomic
phase-admission proof.

Nested callers that already hold the graph lock, allocation-failure rollback,
armed tripwire/log behavior, invalid `YOUNG|OLD`, concurrent/unstable backend
switching, `BIASED`/`DEFERRED`, callback-temporary roots, resurrection metadata,
relocation selection/drain, GC3 holders, global relocation/termination and
performance, stage/bootstrap, fixed point and broad five-GC parity remain
unproved.  A3c graph-lock/no-park integration remains blocked on these holders.
