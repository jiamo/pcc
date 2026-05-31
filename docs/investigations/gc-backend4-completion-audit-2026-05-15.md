# Investigation: backend #4 GenZGC completion audit

## Status
active

## Problem Description

The active goal is to upgrade backend #4, the colored relocating / modern
GenZGC target, toward the latest referenced ZGC design. A large amount of
backend #4 substrate is now implemented and validated, but the goal is not
complete until the implementation covers the actual modern GenZGC page,
remembered-set, relocation, policy, self-host, and threaded-runtime surfaces.

This audit separates current evidence from remaining work. It is intentionally
not a completion claim.

## Concrete Completion Criteria

Backend #4 can be considered complete only when all of these are true:

1. Reference baseline is pinned and compared against the current OpenJDK ZGC
   source used by this repository.
2. C runtime implements the backend #4 object relocation substrate:
   forwarding, read barrier, relocation-set selection, reference updating, and
   supported object copying.
3. C runtime implements a ZPage-shaped allocation and evacuation model, not
   only synthetic owner-side telemetry.
4. C runtime integrates remembered-set/store-buffer state with page ownership
   and relocation policy.
5. Young/old policy is implemented beyond default-young allocation and bounded
   aging telemetry.
6. Fragmentation and page-density policy is backed by real page state and a
   production matrix, not only counters.
7. pcc-Python runtime mirror exposes equivalent ABI and self-host-compatible
   semantics for the surfaces pcc1 uses.
8. Threaded backend #4 stress is covered, including cross-thread store-buffer
   flush, safepoint progress, explicit collect, and pcc1-generated threaded
   programs.
9. Mandatory gates pass:
   `tests/python/test_gc_backend4_production.py`,
   `make -B -C pcc/py_runtime libpy_runtime_pcc_py.a`,
   `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`,
   and fallback ratchet tests.

## Current Evidence

Validated in the current working state:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_owner_remembered_slots
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 3.58s

tests/python/test_gc_backend4_production.py
85 passed in 277.61s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc-bootstrap-profile \
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 63.80s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 59.20s
```

Current backend #4 capabilities with evidence:

- Forwarding/read-barrier/relocation substrate exists and is covered by
  backend #4 production tests.
- Store-buffer and remembered-set telemetry exists, including owner fanout,
  duplicate skips, bounded drains, medium buffer flushing, and cross-thread
  medium flush counters.
- Synthetic ZPage ownership telemetry exists for count, capacity, used bytes,
  fragmentation, page class, dirty pages, young/old pages, and policy score.
- Synthetic ZPage remembered-card pressure exists in the C runtime:
  512 remembered-slot bits, 64 cards, eight slots per card, per-card refcounts,
  card count, card ratio telemetry, and stable ABI counters 102 and 103.
- A first `PccGcZPage` abstraction now exists in the C runtime: owner mapping
  nodes point at a separate page object. This preserves the current one object
  per synthetic page behavior, but starts separating page state from owner
  mapping so later slices can group multiple objects under one page. Focused
  validation after this split:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_owner_remembered_slots
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 3.55s
```
- The pcc-Python runtime mirror now has the same private owner-mapping split:
  zpage nodes store `owner/page/next`, while page state lives in a separate
  mirror page object. The mirror still does not implement pointer-page/card
  grouping, but it no longer keeps page state directly on the owner mapping
  node.
- The C runtime and pcc-Python mirror now also maintain an independent page
  list for population telemetry. Owner mappings still point at one page each,
  but page count/capacity/fragmentation/card telemetry no longer has to iterate
  the owner mapping list. This is the structural precondition for multiple
  owner mappings sharing one page without double-counting page metrics.
- The C runtime relocation selector now snapshots selected page state through
  `PccGcZPageEvacuationCandidate` and
  `pcc_gc_backend4_zpage_candidate_snapshot()` instead of keeping the score
  calculation as an inline owner-node helper. This does not change behavior:
  the score is still fragmentation plus remembered-slot/card pressure plus the
  old-page bonus, and large-object defer logic is unchanged. While relocation
  remains object-level, candidate bytes/backlog continue to use owner object
  size; page fragmentation and page dirty pressure come from the shared page.
  The point is to give the next page-evacuation slice a concrete candidate
  object to carry page, owner, live/used/capacity, dirty-card, and budget state.
- Small and medium backend #4 allocations now reuse an existing same-class
  `PccGcZPage` when capacity remains. Owner mappings are still one per object,
  but multiple owners can now share one page for count/capacity/used/
  fragmentation telemetry. Large allocations deliberately remain one object per
  page until the evacuation protocol is stronger.
- Empty small/medium pages now move to a backend #4 free list instead of being
  freed immediately. `pcc_gc_backend4_zpage_free_pages()` and
  `pcc_gc_backend4_zpage_free_capacity_bytes()` expose the free-list lifecycle
  through telemetry counters 104 and 105. Reallocation of the same page class
  consumes a free page before allocating a new one. The cache is bounded at
  eight small pages and four medium pages; excess empty pages are freed. Large
  pages are still freed immediately.

Validation after the C + pcc-Python `owner -> page` structural split:

```text
tests/python/test_gc_backend4_production.py
85 passed in 278.66s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc-bootstrap-profile \
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 63.67s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 59.32s
```

Validation after splitting page population onto an independent C/pcc-Python
page list:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_owner_remembered_slots
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 3.62s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_gc_backend4_production.py
85 passed in 278.15s

PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc-bootstrap-profile \
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 63.90s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 59.15s
```

Validation after adding same-class small/medium page pooling, owner-specific
remembered pressure for shared-page selector ties, and the object-size
candidate-byte invariant:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_small_objects_share_zpage
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_medium_objects_share_zpage
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_fragmentation_policy_exposes_backlog_and_efficiency
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_selector_uses_zpage_remembered_pressure
4 passed in 15.90s

tests/python/test_gc_backend4_production.py
87 passed in 330.14s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 70.38s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 62.19s
```

Validation after adding the small/medium ZPage free-list lifecycle:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_reuses_empty_zpages_from_free_list
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_small_objects_share_zpage
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_medium_objects_share_zpage
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
4 passed in 11.80s

tests/python/test_gc_backend4_production.py
88 passed in 329.25s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 69.38s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 61.85s
```

Validation after bounding the small/medium free-page cache:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_reuses_empty_zpages_from_free_list
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_free_zpage_cache_is_bounded
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
3 passed in 7.68s

tests/python/test_gc_backend4_production.py
89 passed in 349.62s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 69.76s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 63.78s
```

Validation after making active ZPage reuse generation-aware:

```text
tests/python/test_gc_backend4_production.py
89 passed in 328.00s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 71.57s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 63.14s
```

Notes:

- Small/medium active-page reuse now requires matching page class, matching
  young/old generation, and enough free capacity.
- Empty free-list pages remain generation-neutral at rest and are reset to the
  next owner generation when reused.
- Backend #4 promotion now updates containing-page generation through both
  generation aging and the shared `pcc_gc_promote_young_object()` path used by
  remembered-root promotion.

Validation after adding page-level evacuation candidate byte telemetry:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_candidate_zpage_bytes_count_shared_page_once
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 9.56s

tests/python/test_gc_backend4_production.py
90 passed in 359.90s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 72.90s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 63.69s
```

Notes:

- Existing object-level candidate byte counters are unchanged.
- New ZPage-level counters 106/107/108 count selected page used bytes once per
  page, even when multiple owners on that page enter the object-level
  relocation set.
- This is still telemetry and handoff preparation, not real page evacuation.

Validation after adding an explicit selected-ZPage handoff set:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_candidate_zpage_bytes_count_shared_page_once
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 9.39s

tests/python/test_gc_backend4_production.py
90 passed in 363.05s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.35s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 63.65s
```

Notes:

- `PccGcZPageEvacuationNode` / `pcc_gc_backend4_evacuation_pages` now records
  selected page candidates explicitly in the C runtime.
- The pcc-Python mirror has the matching `pcc_gc_backend4_evacuation_page_head`
  structure and ABI.
- `pcc_gc_telemetry_reset()` reseeds the selected-page set from the current
  object relocation set; `pcc_gc_reset_relocation_set()` clears both.
- Counter 109 reports selected ZPage candidate count. Object relocation still
  performs the actual movement.

Validation after page-local selection expansion:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_candidate_zpage_bytes_count_shared_page_once
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 4.62s

tests/python/test_gc_backend4_production.py
90 passed in 373.06s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 72.88s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.10s
```

Notes:

- Selecting a best page candidate now expands eligible owners on that same
  ZPage before returning to global page selection, bounded by the existing
  object budget.
- This moves selection semantics closer to page evacuation while keeping the
  actual relocation copy object-level.

Validation after selected-ZPage lifecycle cleanup:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_candidate_zpage_bytes_count_shared_page_once
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 4.99s

tests/python/test_gc_backend4_production.py
90 passed in 352.81s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 69.95s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 63.02s
```

Notes:

- Relocating the last pending owner on a selected ZPage now removes that page
  from the selected-ZPage handoff set immediately.
- Counter 109 now represents pending selected page backlog rather than
  historical selected page count since the last reset.

Validation after preventing evacuation targets from reusing selected pages:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_candidate_zpage_bytes_count_shared_page_once
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 4.66s

tests/python/test_gc_backend4_production.py
90 passed in 366.15s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 74.72s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 65.41s
```

Notes:

- Active-page reuse now skips pages present in the selected-ZPage handoff set.
- Relocation targets therefore allocate into another active/free/new page
  instead of refilling the page being evacuated.
- pcc-Python mirror exposes matching ABI and preserves policy/selector count
  semantics needed by self-host, but does not yet implement C-equivalent
  pointer-page/card grouping.
- Self-bootstrap remains green after the latest backend #4 changes.

Validation after source-page accounting/reclamation on relocation copy:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_candidate_zpage_bytes_count_shared_page_once
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 9.43s

tests/python/test_gc_backend4_production.py
90 passed in 372.37s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.08s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.16s
```

Notes:

- A successful relocation copy now removes the source owner from its old
  `PccGcZPage` mapping.
- Source-page used bytes and object count now drop as objects are evacuated.
- Empty small/medium source pages can enter the bounded free-page cache after
  their pending owners have moved.
- Relocation is still object-copy based; this is source-page reclamation, not
  full page-content evacuation.

Validation after remembered-slot retargeting during relocation copy:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_relocation_retargets_remembered_list_slots
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_relocation_retargets_inline_tuple_slots
2 passed in 8.84s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_gc_backend4_production.py
92 passed in 358.65s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.28s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 60.30s
```

Notes:

- C payload relocation now retargets remembered slots from old payload storage
  to copied target storage for list arrays, tuple inline items, dict/set entry
  arrays, instance fields, and common object pointer fields.
- The retarget operation removes remembered slot/card pressure from the source
  ZPage and adds it to the target ZPage, so dirty pressure survives source-page
  accounting/reclamation.
- The pcc-Python mirror keeps only list payload retarget for now. Broad mirror
  retargeting caused a pcc2 stage3 `py_decref` segfault during self-bootstrap;
  narrowing the mirror restored self-bootstrap. Mirror payload-by-payload
  parity remains a separate follow-up.

Validation after extending mirror retargeting to tuple inline slots:

```text
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
1 passed in 0.29s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 72.97s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 63.10s
```

Notes:

- The pcc-Python mirror now retargets tuple inline item slots in addition to
  list payload array slots.
- The broader all-payload mirror retarget remains intentionally split into
  smaller slices because it previously broke pcc2 stage3. This tuple-only slice
  passed self-bootstrap.

Validation after extending mirror retargeting to dict copied-entry key/value
slots:

```text
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
1 passed in 0.29s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 71.96s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.31s
```

Notes:

- The pcc-Python mirror now retargets dict copied-entry key/value slots from
  source entries storage to target entries storage during relocation copy.
- This was kept as a narrow dict-only mirror slice because the earlier broad
  mirror expansion caused a pcc2 stage3 `py_decref` segfault.
- The C runtime broad retarget remains the source of production behavior for
  host backend #4 tests; this mirror slice is self-host parity and bootstrap
  safety work.

Validation after extending mirror retargeting to set copied-entry key slots:

```text
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
1 passed in 0.30s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 72.46s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 63.95s
```

Notes:

- The pcc-Python mirror now retargets set copied-entry key slots from source
  entries storage to target entries storage during relocation copy.
- The public wiring assertion is count-based for the `offset + 8` shape so the
  existing dict key retarget cannot hide a missing set retarget.
- One initial self-bootstrap run failed with a stage2 exit 139. A direct pcc1
  compile to the same bootstrap pcc2 output path did not reproduce, and the
  full self-bootstrap rerun passed. Treat this as a watch item rather than a
  proven set-retarget failure.

Validation after extending mirror retargeting to fixed inline object pointer
slots:

```text
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
1 passed in 0.29s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.56s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.58s
```

Notes:

- The pcc-Python mirror now uses
  `_backend4_remembered_set_retarget_inline_slot(from_owner, to_owner, offset)`
  for fixed inline object pointer slots.
- Covered fixed-layout tags are property, classmethod, staticmethod,
  memoryview, func, iter, gen, coroutine, exception, weakref, thread, and
  task.
- Variable-layout class attrs and instance fields remain separate slices.

Validation after extending mirror retargeting to the class `attrs` inline slot:

```text
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
1 passed in 0.29s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 72.80s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.43s
```

Notes:

- The pcc-Python mirror now retargets the fixed class `attrs` slot at offset
  104 from source class storage to target class storage.
- This slice intentionally does not cover class bases, mro, method arrays, or
  field-name arrays; those are non-object or auxiliary copied arrays and need
  separate treatment if they become remembered-card owners.

Validation after extending mirror retargeting to instance `cls` and field
slots:

```text
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
1 passed in 0.29s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.28s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.90s

tests/python/test_gc_backend4_production.py
92 passed in 374.50s
```

Notes:

- The pcc-Python mirror now retargets the instance `cls` inline slot and each
  copied instance field slot after the existing `n_slots` and size checks pass.
- This closes the mirror's main object-slot retarget parity gap with the C
  runtime for supported relocation-copy payloads. It does not make the mirror a
  real pointer-page/card implementation.

Validation after adding owner+ZPage remembered-card contains/clear APIs:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_card_api_clears_owner_card
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 9.31s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.00s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 62.31s

tests/python/test_gc_backend4_production.py
93 passed in 378.56s
```

Notes:

- C runtime now exposes `pcc_gc_backend4_zpage_contains_remembered_card(owner,
  slot)` and `pcc_gc_backend4_zpage_clear_remembered_card(owner, slot)`.
- `clear_remembered_card` removes all remembered slots for the given owner on
  the same ZPage card as `slot`, while preserving remembered slots on other
  cards.
- The pcc-Python mirror exports ABI-compatible functions, but they are still
  exact-slot approximations because the mirror does not yet implement
  pointer-to-card grouping.
- One initial self-bootstrap run failed with stage2 exit 139. A direct pcc1
  compile to the same bootstrap output path did not reproduce, and the full
  self-bootstrap rerun passed. Keep watching this intermittent pattern.

Validation after exposing selected evacuation drain as a public backend #4 API:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_evacuation_drain_preserves_page_handoff_until_empty
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 9.51s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 75.67s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.70s

tests/python/test_gc_backend4_production.py
94 passed in 385.37s
```

Notes:

- C runtime now exports `pcc_gc_backend4_evacuation_drain(budget)`, backed by
  the existing selected relocation batch.
- `py_runtime.h`, `runtime_abi.py`, and the pcc-Python mirror expose the same
  ABI, so pcc1-compiled runtime code can call the drain path without a host-only
  escape.
- The regression verifies partial drain semantics: a budget-limited drain keeps
  the selected-ZPage handoff alive while pending owners remain, and removes it
  only after the page has no pending selected owners.
- This is still an explicit object-relocation drain over selected pages; it does
  not yet implement real page-content evacuation.

Validation after exposing current selected-ZPage handoff pressure:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_evacuation_page_handoff_reports_current_pressure
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 4.42s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
initial run failed with stage2 exit 139; standalone pcc1 same output path exited 0
rerun passed: 1 passed in 72.42s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 66.66s

tests/python/test_gc_backend4_production.py
95 passed in 390.90s
```

Notes:

- C runtime now exports
  `pcc_gc_backend4_evacuation_page_candidate_bytes()` and
  `pcc_gc_backend4_evacuation_page_dirty_cards()`.
- These APIs summarize the current selected-page handoff set, not historical
  candidate counters. A partial drain lowers the byte pressure while the page
  handoff remains, and a complete drain removes page/byte/card pressure.
- The pcc-Python mirror exposes the same ABI by summing `used_bytes` and
  `remembered_cards` from its synthetic selected page list.
- This makes page handoff pressure observable for later evacuation-budget
  policy. It still does not create a real page allocator or page-copy
  evacuation protocol.
- The intermittent stage2 exit 139 remains a watch item. This run matched the
  prior pattern: direct pcc1 reproduction did not fail, and full bootstrap
  passed on rerun.

Validation after adding page-level selected evacuation drain:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_evacuation_page_drain_moves_whole_selected_page
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_evacuation_page_handoff_reports_current_pressure
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
3 passed in 13.33s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 76.07s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 61.69s

tests/python/test_gc_backend4_production.py
96 passed in 382.54s
```

Notes:

- C runtime now exports `pcc_gc_backend4_evacuation_page_drain(page_budget)`.
- The implementation drains all pending relocation owners on the current
  selected ZPage before consuming another page budget unit.
- This creates a real page-level drain policy boundary while still reusing the
  existing object-copy relocation primitive.
- The pcc-Python mirror exports the same ABI and follows the same selected-page
  loop over the synthetic ZPage list.
- This is still not true page-content evacuation or real page-span memory
  ownership. It is the next policy layer above selected-page handoff.

Validation after adding virtual ZPage span ownership:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_virtual_span_gap
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_small_objects_share_zpage
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
3 passed in 8.09s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 69.36s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 61.64s

tests/python/test_gc_backend4_production.py
97 passed in 409.93s
```

Notes:

- `PccGcZPage` now tracks `allocated_bytes` as a virtual bump cursor.
- `PccGcZPageNode` now records the owner's page-relative `offset_bytes` and
  `size_bytes`.
- Small/medium page reuse uses tail capacity
  `capacity_bytes - allocated_bytes`; freeing an object no longer creates
  immediate bump-reusable space inside an active page.
- New APIs `pcc_gc_backend4_zpage_allocated_bytes()` and
  `pcc_gc_backend4_zpage_reclaimable_gap_bytes()` expose active virtual span
  pressure and active in-page gaps.
- The pcc-Python mirror preserves existing low-level offsets and appends the
  new page/node metadata at the end of the synthetic structs.
- This is still virtual span accounting. Object memory is still allocated by
  `pcc_gc_alloc`; page spans do not yet own or copy raw object memory.

Validation after adding page-owned backing spans:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_backing_span_survives_free_cache
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_virtual_span_gap
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
3 passed in 7.91s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 72.52s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 63.09s

tests/python/test_gc_backend4_production.py
98 passed in 376.96s
```

Notes:

- `PccGcZPage` now owns a backing memory span through `span_base` and
  `span_capacity_bytes`.
- Reset allocates or reuses the page backing span according to page capacity.
- Small/medium pages retain their backing span while cached on the free-page
  list; large pages and cache-overflow pages free the span.
- New APIs `pcc_gc_backend4_zpage_span_bytes()` and
  `pcc_gc_backend4_zpage_free_span_bytes()` expose active and cached backing
  span pressure.
- The pcc-Python mirror appends synthetic span metadata without changing the
  existing low-level offsets used by previously bootstrapped code.
- This is page-owned memory infrastructure only. Objects are still not carved
  out of `span_base`; `pcc_gc_alloc` remains the source of actual object
  addresses.

Validation after exposing owner virtual span location:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_exposes_owner_virtual_span_location
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 4.19s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 68.60s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.50s

tests/python/test_gc_backend4_production.py
99 passed in 403.67s
```

Notes:

- C runtime now exports
  `pcc_gc_backend4_zpage_owner_offset_bytes(owner)`,
  `pcc_gc_backend4_zpage_owner_size_bytes(owner)`, and
  `pcc_gc_backend4_zpage_owner_span_card(owner)`.
- The APIs expose the existing `PccGcZPageNode` virtual bump-span metadata
  through a stable runtime boundary.
- `PCC_GC_BACKEND4_ZPAGE_SPAN_CARD_BYTES` is now public at 512 bytes, and
  owner span-card calculation maps page-relative offsets to a 64-card
  page-local id.
- The pcc-Python mirror exports the same ABI and scans its synthetic
  `owner -> zpage` side table using the appended offset/size fields.
- This is still virtual span location, not real object placement inside
  `span_base`. It is the API boundary needed before moving remembered-card
  accounting away from slot-address cards and toward page-local cards.

Validation after moving C ZPage remembered cards to owner span-local cards:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_owner_remembered_slots
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_card_api_clears_owner_card
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
3 passed in 8.77s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.08s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.91s

tests/python/test_gc_backend4_production.py
99 passed in 405.46s
```

Notes:

- C `pcc_gc_backend4_zpage_note_remembered_card_unlocked()` now derives the
  dirty card from the owner's `PccGcZPageNode.offset_bytes`, not from the
  slot address.
- `pcc_gc_backend4_zpage_contains_remembered_card(owner, slot)` keeps the
  public `slot` argument as a non-null API boundary but checks the owner
  span-local card.
- `pcc_gc_backend4_zpage_clear_remembered_card(owner, slot)` now clears all
  remembered slots in that owner's span-local card. In the current one-owner
  card model, this means all dirty slots for the owner.
- Exact-slot clearing still exists through
  `pcc_gc_backend4_remembered_page_clear_slot(slot)`.
- The pcc-Python mirror already used an owner-level card approximation; this
  slice aligns the C runtime semantics with the owner-span public API.
- This is not yet full page-local remembered-card ownership. The next step is
  making multiple owners that share a real page update one page card table
  directly from page span metadata, rather than using per-owner refcount state
  as the primary dirty-card structure.

Validation after adding shared page-local card refcount regression:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_card_api_clears_owner_card
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_card_refcount_is_shared_by_page_span_card
2 passed in 8.82s

tests/python/test_gc_backend4_production.py
100 passed in 415.66s
```

Notes:

- The new regression allocates two old owners into the same small ZPage and
  confirms both owners map to span card 0.
- Two write barriers create two remembered slots but only one dirty ZPage card.
- Clearing the first owner removes its exact remembered slot, but the shared
  card remains dirty because the second owner still contributes a refcount.
- Clearing the second owner finally clears the card and dirty-page pressure.
- This locks in the intended shared `PccGcZPage.remembered_card_bitmap` /
  `remembered_card_refcounts` behavior before the next slice moves more policy
  to real page-local span metadata.

Validation after adding owner+slot span-card mapping for inline slots:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_inline_slots_use_owner_slot_span_card
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_card_refcount_is_shared_by_page_span_card
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
3 passed in 8.95s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
first run: stage2 exit 139
standalone pcc1 -> pcc2 same path: success
rerun: 1 passed in 74.90s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.56s

tests/python/test_gc_backend4_production.py
101 passed in 418.81s
```

Notes:

- New public API:
  `pcc_gc_backend4_zpage_owner_slot_span_card(owner, slot)`.
- If `slot` is inside the owner's allocation span, C computes the card from
  `owner_offset + ((uintptr_t)slot - (uintptr_t)owner)`.
- If `slot` is an external payload pointer, C falls back to the owner's start
  card. This keeps list/dict/set payload behavior honest until payload memory
  is page-owned.
- `pcc_gc_backend4_zpage_note_remembered_card_unlocked()`,
  `contains_remembered_card()`, and `clear_remembered_card()` now use the
  owner+slot helper.
- A large inline owner with two object slots in different 512-byte span cards
  now creates two dirty cards; clearing one card leaves the other dirty.
- The pcc-Python mirror exports the same ABI but still returns the owner-start
  card approximation, because the mirror has no true pointer-to-card grouping.

Validation after adding registered external payload spans:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_payload_slots_use_registered_payload_span_card
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_inline_slots_use_owner_slot_span_card
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
3 passed in 8.42s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
first run: stage3 exit 139
standalone pcc2 -> pcc3 same path: success
rerun: 1 passed in 72.87s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 65.04s

tests/python/test_gc_backend4_production.py
102 passed in 412.89s
```

Notes:

- C runtime now has a `PccGcZPagePayloadSpanNode` side table and
  `pcc_gc_backend4_zpage_register_owner_payload_span(owner, base, size)`.
- Registering a payload span reserves virtual bytes from the owner's current
  `PccGcZPage.allocated_bytes` bump cursor and adds those bytes to page used
  pressure.
- Owner release removes registered payload spans and subtracts their used
  bytes before the page is recycled.
- `owner_slot_span_card(owner, slot)` now checks owner inline span first,
  then registered payload spans, then falls back to owner-start card.
- The new regression proves external payload slots can occupy different
  page-relative cards and clear independently.
- This still does not make runtime list/dict/set allocators automatically
  page-owned. It is the substrate needed before wiring those allocators to
  register their payload buffers.

Validation after wiring C list/dict/set payload allocators:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_container_payload_allocators_register_spans
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_payload_slots_use_registered_payload_span_card
2 passed in 8.18s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 70.63s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 66.46s

tests/python/test_gc_backend4_production.py
103 passed in 406.15s
```

Notes:

- `py_list_new()` and list growth register the `items` buffer.
- `py_dict_alloc_tables()` and `py_dict_rehash()` register the `entries`
  buffer. The `indices` table is not registered because it has no `PyObject *`
  slots.
- `py_set_alloc_entries()` registers the `entries` buffer, including rehash
  paths.
- `pcc_gc_backend4_zpage_register_owner_payload_span()` now has replace
  semantics: registering a new payload span for an owner removes old spans and
  subtracts their used bytes first. This avoids stale payload pointers after
  `realloc()` or rehash.
- If the current ZPage tail cannot fit the new payload span, registration
  fails and card lookup falls back instead of keeping stale metadata.
- The pcc-Python mirror still only exposes ABI stubs for payload-span
  registration. It does not yet mirror real pointer grouping for Python-coded
  container payloads.

Validation after adding a pcc-Python mirror synthetic payload-span side table:

```text
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
1 passed in 0.32s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.16s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.69s

tests/python/test_gc_backend4_production.py
103 passed in 415.17s
```

Notes:

- `py_substrate.py` now defines
  `pcc_gc_backend4_zpage_payload_span_head`.
- The pcc-Python mirror now keeps synthetic payload-span metadata nodes,
  including owner, base, size, virtual page offset, page pointer, and next.
- Registering a pcc-Python mirror payload span now removes old owner spans
  first, reserves synthetic page-tail virtual bytes, updates page
  used/allocated counts, and returns the virtual offset.
- Owner release removes matching synthetic payload spans and subtracts their
  used bytes.
- This is still not full pointer grouping. The mirror cannot compute
  `slot - base` because the pcc-Python substrate currently lacks a pointer
  difference primitive, so `pcc_gc_backend4_zpage_owner_slot_span_card()`
  remains an owner-start approximation.

Validation after adding `pcc.unsafe.ptr_diff()` and mirror span-local slot
card lookup:

```text
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
1 passed in 0.33s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.62s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 65.62s

tests/python/test_gc_backend4_production.py
103 passed in 422.65s
```

Notes:

- `pcc.unsafe.ptr_diff(lhs, rhs)` lowers to `ptrtoint(lhs) - ptrtoint(rhs)`.
- The pcc-Python mirror now uses `ptr_diff(slot, owner)` for inline owner
  slots.
- The pcc-Python mirror now uses `_backend4_zpage_payload_offset_for_slot()`
  plus `ptr_diff(slot, base)` for registered payload spans.
- This removes the previous owner-start approximation for
  `pcc_gc_backend4_zpage_owner_slot_span_card()` in the pcc-Python mirror.
- The remaining limitation is no longer pointer difference; it is that the
  mirror still uses synthetic metadata rather than a full page allocator /
  evacuation implementation.

Validation after moving backend #4 object allocation onto ZPage backing spans:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_objects_are_carved_from_zpage_span
1 passed in 4.73s

tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
1 passed in 0.19s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
first run: stage2 exit 139
standalone pcc1 -> pcc2: success
rerun: 1 passed in 72.65s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 65.61s

tests/python/test_gc_backend4_production.py
104 passed in 433.41s
```

Notes:

- `pcc_gc_alloc()` now tries `pcc_gc_backend4_try_zpage_alloc(size, flags)`
  before falling back to libc allocation when backend #4 is selected.
- Successful ZPage-backed allocations carry internal
  `PY_FLAG_GC_ZPAGE_ALLOC`.
- `pcc_gc_backend4_zpage_track_alloc_unlocked()` now recognizes objects that
  already live inside a ZPage backing span and records their real page offset
  instead of assigning a synthetic offset.
- `pcc_gc_free_object_memory()` no longer calls libc `free(o)` for
  ZPage-backed objects; memory is owned by the page span and recycled with the
  page.
- The pcc-Python mirror has the matching `pcc_gc_backend4_try_zpage_alloc()`
  path and source-level wiring.
- This is the first real page-backed allocation path. Full page evacuation,
  fragmentation-driven compaction, and large-page lifecycle policy are still
  incomplete.

Validation after adding a relocation target/source page lifecycle regression:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_relocation_targets_use_non_evacuation_zpage
1 passed in 4.54s

tests/python/test_gc_backend4_production.py
105 passed in 427.42s
```

Notes:

- The regression selects two objects on one small ZPage for relocation.
- `pcc_gc_relocate_copy(a)` must allocate `moved_a` on a non-evacuation page,
  not back into the source page.
- `pcc_gc_relocate_copy(b)` must then allocate next to `moved_a` on the new
  target page with offsets `0/128`.
- Once both source owners move, the old source page must enter the free-page
  cache.
- This does not implement full page evacuation by itself, but it locks the
  allocator invariant required by full evacuation: evacuation targets do not
  reuse source pages currently under evacuation.

Validation after adding a dedicated large-ZPage span lifecycle regression:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_large_object_uses_dedicated_zpage_span
1 passed in 4.83s

tests/python/test_gc_backend4_production.py
106 passed in 446.55s
```

Notes:

- A 70000-byte backend #4 object is allocated from a dedicated large ZPage
  span.
- The large page reports owner offset `0`, capacity `131072`, used `70000`,
  and allocated `70000`.
- Releasing the object removes the active large page and restores
  capacity/used/allocated counters to baseline.
- Large pages are not inserted into the small/medium bounded free-page cache.
- This locks current large-page lifecycle behavior. It does not yet implement
  a full large-page fragmentation or evacuation policy.

Validation after integrating selected-ZPage page drain into normal backend #4
step work:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_step_drains_selected_zpage_as_page_budget
1 passed in 5.01s
```

Notes:

- `pcc_gc_step()` now uses the existing selected-ZPage handoff drain for
  backend #4 relocation work instead of only draining one relocation object at
  a time.
- A budget of one selected page can move multiple owners on that page. This is
  closer to the intended ZPage evacuation protocol, but it is still object-copy
  relocation over current page mappings, not a full page-content evacuation
  implementation.
- `test_backend4_genzgc_page_drain_reclaims_source_zpage_to_free_cache`
  now proves the immediate page lifecycle: after
  `pcc_gc_backend4_evacuation_page_drain(1)`, roots read through the forwarding
  barrier to the replacement page, the old owners no longer have active ZPage
  owner offsets, active page population returns to the live target page, and
  the emptied source small page enters the bounded free-page cache.
- The pcc-Python mirror has the same step-path change so bootstrap runtime
  parity stays intact.

Focused validation:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_step_drains_selected_zpage_as_page_budget
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_page_drain_reclaims_source_zpage_to_free_cache
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_evacuation_page_drain_moves_whole_selected_page
3 passed in 12.66s
```

Full backend #4 gate after adding the source-page reclamation regression:

```text
tests/python/test_gc_backend4_production.py
108 passed in 454.94s
```

Validation after making normal backend #4 step select whole pages before
draining:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_step_selects_and_drains_whole_zpage
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 4.29s

tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threaded_backend4_exercises_zpage_allocator
1 passed in 5.53s

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime_pcc_py.a
passed

tests/python/test_gc_backend4_production.py
109 passed in 469.35s
```

Notes:

- `pcc_gc_step()` now uses an internal page-budget selector for backend #4
  before it drains evacuation pages. The public
  `pcc_gc_select_relocation_set(object_budget)` API keeps its existing
  object-budget behavior for explicit tests and callers.
- `test_backend4_genzgc_step_selects_and_drains_whole_zpage` proves a single
  `pcc_gc_step(1)` can select a fragmented small ZPage, relocate both owners,
  resolve roots through forwarding, retire old owner offsets, and return the
  emptied source page to the bounded free-page cache.
- The pcc-Python mirror has the same `_backend4_select_relocation_pages()`
  helper so the bootstrap runtime follows the C runtime step policy.
- The pcc1 threaded backend #4 gate now goes beyond allocator telemetry: after
  running a real-pthread pcc1-built program under `PCC_GC_BACKEND=4`, a second
  pcc1-built probe drives `pcc_gc_step()` until backend #4 evacuated bytes are
  observed and the relocation set is drained.

## Update: large-page normal-step evacuation policy

Backend #4 now has a first large-page evacuation policy instead of only
recording large-object defers. The explicit object-budget API
`pcc_gc_select_relocation_set(object_budget)` still defers objects larger than
the medium-page limit, preserving the existing compatibility surface and
telemetry. The normal `pcc_gc_step()` page-budget selector may now accept a
dedicated large ZPage when it has real internal fragmentation
(`capacity_bytes > used_bytes`) and drain that page through the selected-page
handoff path.

The new regression
`test_backend4_genzgc_step_evacuates_fragmented_large_zpage` allocates a
70000-byte old object on a dedicated 131072-byte large ZPage, runs
`pcc_gc_step(1)`, and verifies:

- one large object is evacuated and `PCC_GC_COUNTER_GENZGC_EVACUATED_BYTES`
  reports `70000`;
- the root read barrier rewrites from the old source to the moved object;
- the old owner leaves the ZPage map with offset `-1`;
- the moved owner remains at large-page offset `0`;
- active large page/capacity/used/allocated deltas remain one live large page;
- large pages are destroyed rather than entering the small/medium bounded
  free-page cache.

Validation:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_step_evacuates_fragmented_large_zpage
1 passed in 4.42s

tests/python/test_gc_backend4_production.py -k 'page_policy_records_candidates_and_evacuated_bytes or large_object_uses_dedicated_zpage_span or step_evacuates_fragmented_large_zpage or step_selects_and_drains_whole_zpage or page_drain_reclaims_source_zpage_to_free_cache'
5 passed, 105 deselected in 20.62s

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime_pcc_py.a
passed

tests/python/test_gc_backend4_production.py
110 passed in 429.07s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 58.72s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 66.37s
```

This closes the first large-page evacuation-policy slice, but not full ZGC
large-page policy parity. Remaining work includes relocation decisions based on
age/fragmentation history, mixed young/old page policy, and true full-page
content evacuation rather than object-copy relocation inside a selected page.

## Missing / Weakly Covered Requirements

The goal is still incomplete for these concrete reasons:

- The ZPage allocator is still partial, but backend #4 object memory is now
  carved from `PccGcZPage.span_base` instead of only being represented by
  synthetic owner telemetry. Small and medium allocations can share active
  pages, reuse empty pages through a bounded free list, and reclaim emptied
  source pages after relocation copy. Large allocation lifecycle,
  fragmentation-driven reclamation/compaction, NUMA/generation-aware page
  selection beyond the current young/old page split, and full lifecycle policy
  are not yet a full ZGC-style allocator.
- Page evacuation remains object-copy based, not full page-content evacuation.
  Relocation operates through the current relocation set; normal step work can
  now choose a whole page, selected pages are explicit handoff records,
  relocation targets avoid evacuation source pages, and emptied source pages
  are reclaimed into the free-page cache. A full page-content evacuation
  protocol is still open.
- Remembered-card integration is still partial. The C runtime has a ZPage
  card bitmap/refcount table, broad C payload slot-retargeting, owner+card
  contains/clear APIs, owner span-local card queries/updates, and inline
  owner+slot span-card mapping. External payload slots can use registered
  payload spans, and C list/dict/set payload allocators now register those
  spans. pcc-Python mirror payload grouping now has synthetic side-table
  metadata, allocation accounting, and span-local card lookup for inline
  slots plus registered external payload slots.
- The pcc-Python mirror still does not implement a full evacuation protocol;
  it now mirrors the page-span allocation entry point, ABI, policy/selector
  count semantics, synthetic page accounting, payload-span metadata, and
  span-local slot/card lookup. It mirrors the `owner -> page` structural split
  plus list/tuple/dict copied-entry/set copied-entry slot retargeting, fixed
  inline object pointer slots, and the class `attrs`/instance inline slots;
  full page evacuation remains open.
- Young/old policy remains partial. There is default-young allocation, bounded
  aging, and population pressure, but not a full GenZGC young/old collection
  policy.
- Fragmentation/density policy remains telemetry-driven. Card density is
  read-only telemetry and intentionally not folded into relocation selection.
- Threaded pcc1-generated backend #4 stress now reaches allocator and page
  evacuation telemetry, but it is still not broad enough to claim
  production-grade concurrent reliability.
- The production verdict across all five GC backends and default selection is
  still open.

## Next Concrete Slices

The next useful implementation slices should be small and independently
validated:

1. Extend the current small/medium page free list into a real `PccGcZPage`
   allocator with fragmentation-driven reclamation and generation-aware page
   selection.
2. Move remembered-card ownership from slot-address cards to owner
   span-local cards, then to the new page abstraction while preserving
   existing counters 102 and 103.
3. Turn `PccGcZPageEvacuationCandidate` into the handoff object for actual page
   evacuation, including evacuation budget and page-local dirty-card pressure.
4. Add focused tests that prove two objects sharing one synthetic page affect
   one page/card structure, not two independent owner pages.
5. Extend pcc1/threaded backend #4 stress after page ownership exists, not
   before.

## Conclusion

Backend #4 has advanced significantly, including real ZPage backing-span
allocation for backend #4 objects and span-local remembered-card lookup for
registered payloads. The active goal is not complete. The next architectural
boundary is full page evacuation and fragmentation-driven page lifecycle
policy.
