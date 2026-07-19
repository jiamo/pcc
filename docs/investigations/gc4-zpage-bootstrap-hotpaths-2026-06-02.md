# Investigation: backend #4 zpage bootstrap hot paths

## Status
resolved for `PCC_GC_BACKEND=4` three-stage bootstrap

## Problem Description
Backend #4 self-bootstrap behaved like a hang during `pcc1 -> pcc2`: worker
processes stayed at high CPU for minutes and the test hit its per-backend
timeout. The user supplied a review trail that first suspected a zpage linked
list cycle, then corrected the diagnosis to pathological zpage lookup cost in
the backend #4 free/allocation paths.

This investigation separates the disproven cycle hypothesis from the actual
runtime hot paths that blocked the `gc4` bootstrap gate.

## Repro
The failing gate shape is:

```bash
PCC_GC_BACKEND=4 env -u LC_ALL uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self[gc4] \
  -n0 -s
```

All heavy runs in this investigation used a parent watchdog that starts the
pytest process in a new process group and sends `TERM` then `KILL` to the whole
group on timeout or manual stop.

Pre-fix observations:

- Stage1 produced `pcc1`.
- `pcc1 -> pcc2` reached the multi-codegen worker batch.
- Ten `pcc1 --pcc-python-multi-codegen-worker worker_*.manifest` processes
  stayed near full CPU.
- Sampling showed backend #4 zpage lookup functions dominating worker time.

## Test [CONFIRMED]
The original focused gate now passes:

```bash
PCC_GC_BACKEND=4 env -u LC_ALL uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self[gc4] \
  -n0 -s
```

Observed result after the final fix:

```text
Bootstrap successful under PCC_GC_BACKEND=4: pcc2 and pcc3 are byte-identical.
1 passed in 115.03s
```

Adjacent focused gates also passed after the zpage changes:

```text
env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_gc_backend.py pcc/py_runtime/py/py_substrate.py

git diff --check

env -u LC_ALL uv run pytest \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_obj_runtime_refcount_primitives_do_not_self_root \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_pcc_unsafe_memory_intrinsics_compile_without_libpython \
  -q -n0
# 3 passed in 27.44s

PCC_GC_BACKEND=4 env -u LC_ALL uv run pytest \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_small_objects_share_zpage \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_objects_are_carved_from_zpage_span \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_reuses_empty_zpages_from_free_list \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_relocation_targets_use_non_evacuation_zpage \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_ownership_telemetry \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_exposes_owner_virtual_span_location \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_payload_slots_use_registered_payload_span_card \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_step_selects_and_drains_whole_zpage \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_page_drain_reclaims_source_zpage_to_free_cache \
  -q -n0
# 9 passed in 49.35s
```

## Proposals

- No.1 zpage offset-24 double-link cycle     [DENIED]
- No.2 index zpage owner/page lookups     [CONFIRMED]
- No.3 make frame-leave index misses O(1)     [CONFIRMED]
- No.4 make zpage allocation active-page-only     [CONFIRMED]
- No.5 index payload span removal immediately     [DENIED for current hotspot]

## No.1 zpage offset-24 double-link cycle

### Code Change
No code change. This was a diagnosis candidate only.

### DENIED
The zpage node layout in the pcc-Python runtime is not the same as the other
newly indexed/double-linked lists. The old zpage node shape used owner, page,
next, offset, and size; offset 24 was payload offset, not a `prev` pointer. The
user's later reread also corrected this: the observed worker saturation was
finite pathological scanning, not an actual linked-list cycle.

## No.2 index zpage owner/page lookups

### Code Change
Backend #4 zpage nodes now maintain direct indexes in both runtime tiers:

- owner -> zpage node
- page -> first owner node on that page

The pcc-Python mirror expanded zpage nodes to carry global `prev` plus
per-page `page_next/page_prev` links. The C runtime mirrors that structure in
`PccGcZPageNode`. Link/unlink paths insert/remove both indexes, clear indexes
on page recycle/destroy, and use index lookup in owner telemetry, remembered
slot/card accounting, relocation owner lookup, and page-owner lookup.

### CONFIRMED
This removed the original `free N objects -> scan zpage list N times` shape.
After this change, samples no longer placed the main cost in
`_backend4_zpage_remove` / `_backend4_zpage_find_owner_for_page`. The next
visible bottleneck moved to `pcc_gc_note_frame_leave`, then to allocation-time
`_backend4_zpage_find_reusable_page_for_gen`.

## No.3 make frame-leave index misses O(1)

### Code Change
`pcc_gc_note_frame_leave` now returns immediately when the frame index has no
entry for the slots pointer. It no longer falls back to a linear scan of the
frame list for the common zero-root / unregistered-frame miss path.

`pcc_gc_note_frame_enter` also cleans up consistently when frame-index insertion
fails, so the fast leave path does not strand duplicate nodes.

### CONFIRMED
After No.2, sampling a stuck `gc4` stage2 process showed
`pcc_gc_note_frame_leave` as the dominant top-of-stack function. After this
change, a later sample of worker processes showed frame leave only as minor
background cost; the dominant path moved again to zpage allocation lookup.

## No.4 make zpage allocation active-page-only

### Code Change
The zpage allocation fast path no longer scans the full active zpage list
looking for reusable pages after the current active page misses. Backend #4
zpages are bump-allocated spans; old pages generally cannot reuse interior holes
for ordinary allocation. The allocation path now:

1. tries the class/generation active page,
2. falls back to the bounded free-page cache,
3. allocates a new page if needed.

`_backend4_zpage_find_page_for_addr` still scans as a containment fallback, but
it first checks the active small/medium pages; this covers the common
`pcc_gc_backend4_try_zpage_alloc` followed by `pcc_gc_note_object_allocated_sized`
tracking path.

The same change was made in both `pcc/py_runtime/py/py_gc_backend.py` and
`pcc/py_runtime/src/py_gc_backend.c`.

### CONFIRMED
Before this change, a 5-second sample of a `pcc1` worker still showed:

```text
user_py_gc_backend__backend4_zpage_find_reusable_page_for_gen  2858 samples
```

After the active-only allocation change, the focused `gc4` bootstrap completed
in 115.03 seconds and reported byte-identical `pcc2`/`pcc3`.

## No.5 index payload span removal immediately

### Code Change
No code change in this slice.

### DENIED for current hotspot
Static review is correct that `_backend4_zpage_remove_payload_spans(owner)`
still scans the payload-span list and may become O(N^2) if many strings,
lists, or dicts register payload spans and are then freed object-by-object.

However, dynamic samples taken after No.2 and No.3 did not show
`_backend4_zpage_remove_payload_spans` as the active bootstrap blocker. The hot
path remained `_backend4_zpage_find_reusable_page_for_gen`, and fixing that path
made `gc4` bootstrap pass. Payload-span indexing remains a valid follow-up
performance hardening item, but it was not required to close this `gc4`
bootstrap failure.

## Report
The landed causal chain is No.2 + No.3 + No.4:

- No.2 removed the confirmed zpage owner/page O(N) lookup cost in free,
  telemetry, and relocation-owner paths.
- No.3 removed the next exposed frame-leave O(depth) miss path.
- No.4 removed the final observed allocation-time full zpage-list scan by
  making ordinary zpage allocation active-page-only.

Two review hypotheses were useful but not the final bug:

- The offset-24 zpage cycle hypothesis was wrong; offset 24 was not a zpage
  `prev` pointer in the old layout.
- Payload-span removal is still a static performance risk, but it was not the
  hotspot in the samples that blocked `gc4`.

Current evidence boundary:

- `PCC_GC_BACKEND=4` three-stage self bootstrap is green and byte-identical.
- The full five-GC matrix was started after the `gc4` pass, but the user
  redirected the work to documentation only. Later process hygiene found
  residual full-matrix process groups for `gc1`, `gc2`, `gc3`, and `gc4`; they
  were killed without recording a matrix verdict. No full five-GC bootstrap
  success is claimed here.
