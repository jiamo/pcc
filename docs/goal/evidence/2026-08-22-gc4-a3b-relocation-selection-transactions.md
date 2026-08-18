# GC4 A3b relocation-selection transactions

Date: 2026-08-22

## Claim

For one stable Backend 4 selection, valid managed values, threads enabled and
the default `ATOMIC` refcount strategy, the C transition oracle and strict
freestanding pcc-Python runtime now keep allocation and safepoint work outside
the graph-locked commit portion of relocation selection.

Object selection first allocates candidate-plan nodes outside the graph lock.
Each locked tenure commits at most 16 selected objects, then unlocks before
freeing unused plan nodes or polling.  The strict candidate scorer no longer
calls ordinary managed provider functions from the locked selection loop; it
uses the freestanding global/index state and one explicit cross-object
forwarding-target predicate.

Page selection first snapshots one eligible ZPage, allocates the page node and
the complete relocation-node capacity outside the graph lock, then commits at
most 16 objects per tenure.  The first commit revalidates page mapping count;
if the page grew after preflight, the incomplete plan is discarded outside the
lock and selection retries.  A page with constructor-pending allocations is
not eligible.  The page-local `object_head/page_next` chain is the authoritative
membership list during commit, and the strict mirror uses the actual ZPage-node
size field at offset 32.

The public relocation policy, score, candidate flags, page handoff and whole-
page evacuation behavior remain intact.  The newly callable C page selector is
runtime-internal and has no public header or C-API export.

## Frozen source identity

```text
84825af84d4bdc8cf9347ba6098b707ec93dc6af6ac9ae8e137b4863da9f6b50  pcc/py_runtime/src/py_gc_backend.c
c8b5203a97a59cc58b22fef9e3e22cebc4205d4ffca0c74d37699003844fff57  pcc/py_runtime/py/freestanding_gc_relocation_selector.py
674069031d9899e17c7082f4f514887dcd5cc382823e01ef8aee9a953ccb8369  pcc/py_frontend/codegen/runtime_abi.py
9cc6d8c3c132acd5a466e5778f26adb85c1ab5696cc896afd36e15b4252ecf1f  tests/python/test_gc_threading_substrate.py
f1f3d37af8d12c61a232898f2390b37a6643abbe15ddfe74848b0196f1b35118  tests/python/test_freestanding_gc_relocation_selector.py
40456c31f5d59a2ea8ca38a1b4f9221d316f7734b9c25a54d313ac77e4fe8b0a  tests/python/test_gc_backend4_production.py
```

On this identity, whole-tree `git diff --check`, isolated Python compilation,
and threaded/threads-off C syntax checks are green.  The C syntax checks report
the same ten pre-existing warnings in both modes and no new warning.

## Genuine RED evidence

The original object selector polled while retaining the graph lock.  The
deterministic true-pthread C probe stopped the world after the worker entered
public selection, then acquired the same public graph lock.  The worker parked
at the in-lock poll while retaining that lock and the child watchdog expired:

```text
test_colored_relocation_selector_polls_only_after_releasing_graph_lock[c]
1 failed in 10.45s
```

After the C split, the same node passed in 6.75 seconds.  The first strict
attempt used C-only root-slot symbols and failed to link; that was a harness
correction, not product evidence.  With a strict-compatible graph-lock witness,
the old strict selector still timed out after 10 seconds because ordinary
provider work could poll or allocate while graph-locked:

```text
test_colored_relocation_selector_polls_only_after_releasing_graph_lock[pcc_python]
1 failed in 11.02s
```

The preallocated strict object transaction then passed in 123.51 seconds.  Its
durable build log is
`build/gc4-a3b-relocation-selector-strict-preallocated.log`, SHA-256
`7d1c61c5933cb04a06f1b1a635159eb140e8bbac00dbd705e7d196d41c35e078`.

The page-selector tracer produced two independent product REDs.  The old C
page loop selected all 32 objects in one graph-lock tenure; the stopped-world
owner observed `page-selected=32` where the per-tenure contract requires 16,
and pytest reported `1 failed in 6.66s`.  The strict partial implementation
failed in 0.61 seconds with `page-selected=0`, exposing its unsafe pre-lock /
ordinary-provider shape before a transaction could be committed.

The first strict page-transaction implementation returned 31 rather than 32.
Increasing the page budget and revalidating the preflight mapping count did not
change that result; the retained diagnostic run reported all 32 identities on
the same page, with only object zero missing the candidate flag.  The actual
bug was the strict selector reading ZPage-node offset 24 (owner) as object size
instead of offset 32 (size).  The final code and source gate pin offset 32.

The final strict page node used:

```bash
gtimeout 600s zsh -o pipefail -c 'gtimeout 540s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short "tests/python/test_gc_threading_substrate.py::test_colored_relocation_page_selector_polls_only_after_releasing_graph_lock[pcc_python]" 2>&1 | tee build/gc4-a3b-relocation-page-selector-strict-final.log'
```

Result: `1 passed in 123.47s`; log SHA-256
`774eabde35121dacdfa6c1a8e00f9e0b5639e0828608703e237cbb039bf27826`.

## Final focused gates

The final selector packet was rerun against the explicit current threaded
strict archive:

```bash
gtimeout 120s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY \
  PCC_RUNTIME_ARCHIVE=/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/b241c71c3d82c6f9ff6a4bb4-threaded-pcc-py/libpy_runtime_pcc_py.a \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_gc_threading_substrate.py::test_colored_relocation_selector_polls_after_graph_unlock_in_c_and_strict \
  tests/python/test_gc_threading_substrate.py::test_colored_relocation_page_selector_polls_only_after_releasing_graph_lock \
  tests/python/test_gc_threading_substrate.py::test_colored_relocation_selector_polls_only_after_releasing_graph_lock \
  tests/python/test_freestanding_gc_relocation_selector.py
```

It collected the exact static contract, C/strict page selector, C/strict object
selector, strict source owner, LLVM/self closure, policy and archive-owner nodes:
`10 passed in 3.73s`.

The exact policy and whole-page evacuation neighbors were rerun with the same
archive:

```bash
gtimeout 120s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY \
  PCC_RUNTIME_ARCHIVE=/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/b241c71c3d82c6f9ff6a4bb4-threaded-pcc-py/libpy_runtime_pcc_py.a \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_page_policy_records_candidates_and_evacuated_bytes \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_selector_prefers_fragmented_zpage \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_step_evacuates_fragmented_large_zpage \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_candidate_zpage_bytes_count_shared_page_once \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_selector_uses_zpage_remembered_pressure \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_selector_prefers_old_zpage_age_pressure \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_selector_skips_zero_benefit_zpage \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_evacuation_incomplete_batches_track_budget_backlog \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_evacuation_page_handoff_reports_current_pressure \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_evacuation_page_drain_moves_whole_selected_page \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_step_drains_selected_zpage_as_page_budget \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_step_selects_and_drains_whole_zpage
```

Result: `12 passed in 0.94s`.

The last neighbor initially returned one unit from the already completed
generation-aging phase: two objects had their `YOUNG` header bits manually
cleared but still occupied two pending-young worklist entries.  The test now
explicitly drains those two maintenance entries, proves the relocation set is
still empty, resets telemetry, and only then checks whole-page select/drain.
This records phase ordering rather than weakening the page-selection result.

No broad suite, stage/bootstrap chain, performance profile or five-GC matrix
was run for this slice.

## Strict archive receipt

The final archive key is
`b241c71c3d82c6f9ff6a4bb4-threaded-pcc-py`.  The production provenance
verifier passed against the current `pcc/py_runtime` source root.

```text
b967b409831c815e9534ccbc4bd5b6dc942d199100a4ca5ca8f117c7b1b2c181  libpy_runtime_pcc_py.a
e785a6d22d7869617cd781b1e7b6a14e53a7f3f736e09bbbb73b85a003390789  libpy_runtime_pcc_py.a.provenance.json
71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda  libpy_runtime_pcc_py.a.capi_syms
1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1  libpy_runtime_pcc_py.a.target
c0c75ce24c37a485d196e7a09f37ab8cd585183eaf1a719f35bc160c5e075e49  .pcc-threaded-pcc-py-complete
```

The manifest reports schema `pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target
`arm64-apple-darwin25.5.0`, 186 pcc-Python members and 444 C-API symbols.  Every
member reports `uses_host_cc=false`.  The selector object receipt records the
frozen strict source hash above.  The completion marker is schema
`pcc.runtime-build-cache.v4` and matches the archive, manifest, C-API inventory
and target hashes.

## Review and boundaries

After the user's request to minimize agent use, the final selector identity,
tests, receipts and claim wording were reviewed locally without starting or
contacting a sub-agent.  This evidence therefore makes no independent dual-
review claim.

This closes the direct relocation object/page selector edges to plan allocation
and safepoint work for the stated stable outermost path.  It does **not** make
selection a fully bounded graph-lock leaf.  Candidate discovery still scans
the global ZPage/object and page-local object lists while locked.  The preflight
plan prevents silent under-capacity after page growth, but does not prove a
formal page-address epoch/ABA or starvation contract across concurrent page
destroy/reuse.  Constructor-pending exclusion is pinned by source/policy gates,
not by a dedicated constructor-race runtime handshake.  Low-level atomic
operations are not formally wait-free.

Relocation drain/copy/remap/retirement holders, the remembered-root
owner/referent bounds and enqueue-to-phase-admission race, GC3 graph-lock
holders, nested outer-lock callers, invalid-state tripwires/logging, refmeta,
`BIASED`/`DEFERRED`, concurrent/unstable backend switching, callback roots,
resurrection restoration, physical relocation, A3c no-park integration,
stage/performance, fixed point and broad five-GC parity remain unproved.  The
parent task remains `IN_PROGRESS`.
