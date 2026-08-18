# Granule map v2: verified history and current open boundary (updated 2026-08-22)

Row: `ARCH-P0-PROVENANCE-GRANULE-MAP`

Status: **IN_PROGRESS.** This file records historical focused checks and stage
measurements, then corrects their claim boundary for the current worktree. It
now proves bounded current-source S1 actual-pthread, allocator-family,
exact-provenance/layout and focused GC3/4 moving-lifecycle gates; it does not
prove a current S2 candidate, allocation-failure rollback, universal
pthread/lifecycle safety, stage2 parity with stage1, host-CPython parity, fixed
point, or five-GC acceptance.

Mode labels: host pcc compiled the focused probes; recorded compiler numbers
used a Darwin arm64 pcc1 built with self backend, no libpython and
`ir-scaffold=on`. The measurements below were not one receipt-bound paired
experiment and must not be mixed into a speedup claim.

## Current frozen S1 source identity and mechanism

Current source is stable at these hashes bound to the focused runtime receipt:

```text
freestanding_allocator.py  76a996a36a01d399bf3ac5d5dcd91b27ce6f36a2e6bbb391b2fd78f72db90781
py_gc_backend.py            94ccd807861a4befb969409bd39765573e5758a57aa74a2970b5e3c4697de5d6
py_obj.py                   ef2d5a628333dac965316aacb3ff373d3cca10fe5b8cc945fde71df06c070b11
py_gc_backend.c             4f16cd515f3f93e2cdb568446c82b5a1fb30990b3ef1f08797d98e7e550350fe
py_gc_index_table.c         3f3be36de8e77cd305531e8ff27b90e2e1c37f2b8320d055c4da954ab4f9180d
py_runtime.h                6f6f833edf79465187b85d719147e82ef632900c1b31578de4788124dbffdcec
test_gc_granule_map.py      164118aacfa6d5e901649605e0d4ba74f60d02ccb7f6990d40304bda5f56d4ba
```

The allocator splits ordinary Python objects from raw allocations and adds a
4 KiB-granule metadata map with one stable span descriptor per slab. Public
slab registration acquires the allocator lock itself. Before publishing the
first of sixteen keys, it checks all duplicate keys, reserves capacity for all
sixteen entries, and allocates the span; bind then cannot allocate, grow or
rebind. Production unbind/rollback was removed, so the published table is
append-only. Growth publishes a new table with release/acquire ordering and
keeps old tables alive.

Accounting boundary: every successful granule-table generation and every
64 KiB immortal span-descriptor arena is now added to
`pcc_allocator_mapped`, so `pcc_os_heap_capacity_bytes()` includes this retained
metadata. These pages deliberately do not increase caller payload
`requested`/`usable`. A separately labeled
`pcc_allocator_metadata_mapped` counter lets the focused gate prove the exact
metadata component; it is a diagnostic subdivision, not extra mapped bytes.
Old table generations and span arenas remain mapped for reader safety, so the
counter is monotonic for this metadata by design.

The exact managed-pointer index is again the production provenance authority;
all query/register/unregister/migration/freeing paths use it unchanged.
Forwarding indexes and object-node sweep lists are separate and were not
replaced. Python and C expose an explicit
`pcc_gc_granule_s2_candidate_positive` helper for a future isolated A/B, but a
current-source search finds only its Python definition, C definition and header
declaration—zero production callers. Therefore the proposed fast-positive plus
five write-site skips is **not landed production S2** and has no accepted
correctness or performance claim.

## Historical focused checks — narrow evidence only

A prior checkpoint reported the granule/provenance focused cases passing and
also reported allocator/layout, fallback-ratchet and root-sizing checks. Those
results establish useful serial behavior on that checkpoint; they are not a
receipt for a later source state.

The earlier statement that `test_gc_granule_map.py` supplied a
“real-pthread 4-thread concurrent grow” proof is **withdrawn**. In the tested
pcc-Python path, `Thread.start()` called the target synchronously, so the four
workers did not overlap. Setting `PCC_WITH_THREADS=1` around compilation also
did not by itself prove that the content-addressed archive selected a pthread
kernel. The test therefore proved serial grow/read behavior, not lock-free
reader/writer publication, and it also called the single-writer map API from
multiple logical writers instead of respecting allocator-lock ownership.

The first replacement actual-pthread receipt (`3 passed in 282.43s`, test SHA
`ec9c9718`, log SHA `13259aa4`) also made one claim that is now
**withdrawn**. Its `writer_active` flag covered the writer's whole 600-slab
loop, including allocation, publication bookkeeping and explicit
`sched_yield()` calls. A reader counted as overlapping merely by running while
that broad flag was set. The receipt exercised a real pthread runtime and
observed no lookup errors, but did not prove that a complete reader lookup
overlapped one particular registration call, nor that it overlapped a table
growth publication. It is superseded by the per-registration epoch receipt
below.

Static checks from the older checkpoint—Python AST parse, `git diff --check`,
C `clang -fsyntax-only` (five pre-existing range warnings), and freestanding
allocator no-libpython LLVM-IR-to-object compilation—remain historical narrow
evidence. They do not qualify the current hashes by themselves.

## Current focused S1 receipt — green at its bounded gate

Both overbroad overlap claims above were replaced, not relabeled. On the frozen
hashes in this file, after one repaired single-node run passed, the following
exact fail-fast whole-file command completed from the content-addressed cache:

```text
gtimeout 150s zsh -o pipefail -c 'gtimeout 120s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_granule_map.py 2>&1 | tee build/granule-map-s1-closure-final-v3.log'
```

Result: `3 passed in 5.67s`. The test source SHA-256 is
`164118aacfa6d5e901649605e0d4ba74f60d02ccb7f6990d40304bda5f56d4ba`;
the durable log SHA-256 is
`51d4790218e77bc7f334f7398dc96c64a9ee9f6485f1337acd9eb6bf0ebde060`.
The short elapsed time is a hot content-addressed-cache receipt, not a cold
runtime-build benchmark.

This receipt proves only the exercised S1 boundary:

- object/raw classification remained exact across requested GC0..4, including
  object free -> same-size raw allocation -> object reuse without crossing
  free-list families;
- invalid and duplicate preflight remained fail-closed. A late-overlap case
  whose first fifteen candidate granules were absent and whose sixteenth alone
  was already registered returned failure and left all first fifteen keys
  unpublished;
- a `pcc_gc_alloc(20000, ...)` allocation stayed outside the slab map, entered
  the exact managed-pointer set, and left that exact set on free under every
  requested GC0..4 backend;
- growth preserved all sixteen 4 KiB granule keys for each of 600 slabs
  (9,600 keys total);
- one writer created with `pcc_thread_start` and three lock-free readers used a
  sequentially-consistent per-registration odd/even epoch. The writer set the
  odd epoch immediately before the public registration call and the even epoch
  immediately after it, with no unrelated work or yield inside that source
  window. A reader classified a stress observation only when all of its lookups
  saw the same odd epoch;
- the harness separately required positive same-odd call-window observations
  for ordinary registrations and the known table-grow ordinals 8, 16, 32, 64,
  128, 256 and 512 (initial capacity 256; sixteen keys per slab). One
  permanently unregistered, page-aligned sentinel was queried in each same-odd
  lookup window; both
  ordinary and grow windows independently required positive negative-query
  observation, `span == NULL`, and `kind == 0`. Thus the gate exercises both
  zero false negatives for published keys and zero false positives for an
  absent key under real-pthread same-odd public-call stress; and
- table-generation and span-arena page bytes matched the exact expected
  metadata sum, the corresponding mapped-capacity deltas matched, and metadata
  registration did not change requested/usable payload counters.

This does **not** inject a `page_alloc` failure. If reserve successfully grows
the immutable table and the later span allocation fails, no slab key is
published, but the new table generation remains mapped and accounted; there is
no metadata rollback claim. A separate adversarial review also found that
exact-index publication failure after object storage is obtained lacks
origin-aware allocation rollback. That finding is now the independent
`ARCH-P1-EXACT-INDEX-ALLOC-ROLLBACK` task and was deliberately not patched in
this S1 slice. The gate also makes no S2, end-to-end performance, broader
GC3/4 relocation/forwarding, stage, fixed-point or five-GC acceptance claim.
The epoch is a harness boundary around the public call, not a runtime hook at
the internal table/key publication store; this receipt therefore does not claim
that a sampled lookup hit that exact machine-instruction instant.

## Current exact-provenance/layout receipt — bounded green

On allocator SHA `76a996a3`, the adjacent exact-provenance/layout gate
completed:

```text
gtimeout 150s zsh -o pipefail -c 'gtimeout 120s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_runtime_pointer_provenance.py tests/python/test_runtime_layout_contract.py 2>&1 | tee build/granule-provenance-layout-current-v2.log'
```

Result: `5 passed in 1.50s`; log SHA-256
`f6defbf3e1e5157efce7c7558708e35bc521a911a2a7d9261cccb8d13241bd65`.
The five nodes prove the one exact managed-pointer decision, publication before
header consumers, C-runtime and pcc-Python provenance under requested GC0..4,
and the C/pcc-Python layout contract on the current allocator hash. They do not
exercise the stronger GC3/4 relocation/forwarding lifecycle gate; that current
receipt follows.

## Current GC3/4 moving-runtime receipt — bounded green

The stronger focused moving-lifecycle gate used this explicit current-hash
threaded pcc-Python runtime archive:

```text
gtimeout 150s zsh -o pipefail -c 'gtimeout 120s env -u LC_ALL PCC_RUNTIME_ARCHIVE=/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/120492e512191cf83498799d-threaded-pcc-py/libpy_runtime_pcc_py.a uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_oldifies_copy_for_remembered_child tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_release_of_forwarded_source_consumes_source_ref tests/python/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_targets_wait_for_phase_reset tests/python/test_freestanding_gc_forwarding_retirement.py::test_forwarding_retirement_matches_c_oracle_across_three_remap_epochs 2>&1 | tee build/granule-gc34-moving-current-v2.log'
```

Result: `4 passed in 2.67s`; durable log SHA-256
`a1758ad91783c5c97becea3d0b82514cf7458c30a0ddb36a7566b108a39b6899`.
The four nodes cover GC3 minor-refill oldify-copy, GC3 forwarded-source
release, GC4 targets waiting for phase reset, and C/pcc-Python forwarding
retirement across three remap epochs. The bound archive SHA-256 is
`7574be06f51ac71edd52d8bf054bad21f4e28abdc2413d272a55a8e093d35ff0`;
its provenance manifest SHA-256 is
`36239bdca9cc8fbe4fce96438a6a41a54a81e8b9b858a5fe04147ef82bb3f13c`
and explicitly records freestanding allocator source SHA `76a996a3`.

Mode boundary: these tests exercise the no-libpython pcc-Python production
runtime from an immutable content-addressed threaded archive, but their test
harnesses are clang-linked and the archive emitter uses the llvmlite target
machine. They are **not self-backed**, pcc1, stage, S2 or end-to-end
performance evidence.

## Historical stage and worker measurements — not a paired acceptance

- Retained graph-lock control artifact:
  `build/gc0-lock-candidate-v1/stage2-profile-control/stage1.result.json`
  records stage1 `wall_ms=63908` (63.908s) and compile wall 60.217s.
  Older hot-stage1 history is 71-90s.
- Reported later granule-source stage1: rc=0, 133.6s, but no phase receipt was
  retained. It is not claim-grade or comparable to the graph-lock control; if
  reproduced on current source it is a stage1 regression to diagnose.
- A reported cold stage1 was about 351s, also without a paired receipt.
- Reported later granule-source stage2: rc=0, 421.1s with a working pcc2 but no
  phase receipt. It is not a claim-grade pair with 133.6s. The required
  `stage2 <= stage1` condition remains unproved, and it may not be made easier
  by allowing stage1 itself to regress.
- Reported pcc1 function smoke: a def-containing program compiled through
  `-o`, ran, and printed 42.
- Historical single-arm module98 smoke: 15.34/15.44/15.65s, max RSS about
  2.68GB, deterministic output. This was not a receipt-bound control/candidate
  A/B and proves no speedup.
- No host-CPython control was captured for the current comparison.

Keep one separate artifact label exact: the graph-lock no-guard control at
`build/gc0-lock-candidate-v1/stage2-profile-control/stage2.result.json`
records stage2 `wall_ms=357685` (357.685s). Its enclosing `/usr/bin/time`
record is 422.54s and 9.032GB max RSS. **422.5s is outer harness wall, not
stage2.** That control is a different source/runtime experiment from the
reported 421.1s granule stage and cannot be substituted for it.

## Current required continuation

1. Keep `page_alloc` failure injection and origin-aware exact-index allocation
   rollback explicit open boundaries. The latter is tracked separately as
   `ARCH-P1-EXACT-INDEX-ALLOC-ROLLBACK`; do not infer either rollback from the
   current successful-allocation receipt.
2. Current-source stage1 construction and a def-containing compile/run are now
   green under the precise mode boundary recorded in
   `2026-08-22-current-source-stage1-runtime-cache-v3.md`: compiler modules use
   the self backend with no libpython and pcc-owned linking, while the bundled
   pcc-Python runtime objects were emitted by the llvmlite target machine.
   Next run the receipt-bound frozen module98 A/B. Stop before stage2 on any
   red gate or a missed pre-registered performance bar.
3. Only after focused acceptance, recapture current-source stage1 hot and cold
   before stage2. Diagnose any repeat near 130s against the retained 63.908s
   control and older 71-90s history; the stage2 target cannot be relaxed by a
   slower stage1. Then capture same-source/same-knob stage2 and host-CPython
   controls with wall/CPU/process-tree RSS/output, fallback and linkage
   receipts.
4. Finish with sequential pcc1→pcc2→pcc3 and the five-GC matrix before any
   `DONE_STRONG` claim.
