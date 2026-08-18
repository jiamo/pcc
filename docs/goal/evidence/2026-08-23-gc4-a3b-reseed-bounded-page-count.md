# GC4 A3b relocation-reseed bounded page count

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b holder sub-boundary confirmed; parent task remains
`IN_PROGRESS`.

## Claim boundary

Relocation-epoch reseed in the C and strict pcc-Python roots now counts its
required evacuation nodes directly from the authoritative evacuation list in
batches of at most 16 per graph-lock tenure. The former unbounded page-list
walk with a nested relocation-list membership scan is no longer called from
reseed.

One reseed count owner serializes concurrent reseeds across batch unlocks. A
raw evacuation-node cursor is advanced by selection removal, relocation-copy
page detach, full reset and detach-all before any matching node is recycled.
Every evacuation-list add/detach increments a graph-locked revision; a change
during counting resets the count and cursor to the current head. Waiters and
non-final batches safepoint outside the graph lock.

The proven count is still fed into the existing private-node preparation and
locked revalidation protocol. The deterministic growth and allocation-failure
behavior from the preceding slice therefore remains intact.

This does not close reseed's later unbounded relocation aggregate or page
rebuild/commit scans. The reseed owner serializes only reseeds; it is not a
mutator-quiescence or page-lifetime lease. A3c and the remaining GC3/callback/
log holders remain open.

## Genuine RED

`test_relocation_reseed_required_page_count_is_bounded_and_restartable` was
added first and failed because reseed still called the old unbounded helper:

```text
1 failed in 0.33s
AssertionError: 'pcc_gc_backend4_reseed_page_count_unlocked' is contained here
```

## Implementation

- C and strict state add an exact reseed-count owner, cursor and revision.
- Required count consumes at most `PCC_GC_SAFEPOINT_BATCH` / 16 existing
  evacuation nodes, unlocks and safepoints, then resumes only if the revision
  remains stable; otherwise it restarts from the authoritative head.
- C page add/detach/reset and strict selector, relocation-copy, reset and page
  helper paths update the revision and advance the cursor before retirement.
- A 24-page probe uses 60000-byte medium allocations, so each accepted object
  occupies a distinct 65536-byte page and necessarily crosses the 16-node
  boundary.

## Focused evidence

All pytest commands stopped at the first failure.

1. The deterministic C and strict pthread probe pauses reseed immediately after
   the first 16-page count batch, performs a concurrent full reset that unlinks
   and recycles all 24 nodes, then resumes. Both roots restart to an empty
   snapshot without UAF and subsequently recover all 24 pages / 1440000 bytes.

2. The final hot-archive packet covers the source contract, exact selector/
   relocation-copy raw closure, C/strict forced cursor invalidation, prior
   C/strict count-to-prepare growth and OOM, and prior four-thread reset/reseed:

   ```text
   gtimeout 120s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend4_production.py::test_relocation_reseed_required_page_count_is_bounded_and_restartable tests/python/test_freestanding_gc_relocation_selector.py::test_relocation_selector_has_one_strict_source_owner tests/python/test_freestanding_gc_relocation_copy.py::test_relocation_copy_has_one_strict_source_owner tests/python/test_gc_backend4_production.py::test_c_reseed_count_cursor_survives_concurrent_full_reset tests/python/test_gc_backend4_production.py::test_strict_reseed_count_cursor_survives_concurrent_full_reset tests/python/test_gc_backend4_production.py::test_c_reseed_forces_plan_growth_and_allocation_failure tests/python/test_gc_backend4_production.py::test_strict_reseed_forces_plan_growth_and_allocation_failure tests/python/test_gc_backend4_production.py::test_c_concurrent_reset_reseed_revalidates_prepared_plan tests/python/test_gc_backend4_production.py::test_strict_concurrent_reset_reseed_revalidates_prepared_plan 2>&1 | tee build/gc4-a3b-reseed-count-final.log'
   9 passed in 1.87s
   ```

3. The true relocation-copy/page-detach neighbor remained green in both roots:

   ```text
   gtimeout 240s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend_relocating.py::test_colored_relocating_targets_wait_for_phase_reset tests/python/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_targets_wait_for_phase_reset 2>&1 | tee build/gc4-a3b-reseed-count-relocation-parity.log'
   2 passed in 7.22s
   ```

4. All four affected strict modules compiled under
   `--backend self --python-libpython=off --ir-scaffold=on --python-library`;
   the receipt is `build/gc4-a3b-reseed-count-closures-final.log`. Python
   syntax, C syntax with `PCC_WITH_THREADS=0/1`, and `git diff --check` passed.

## Frozen identities

```text
8b061bd21e3de36755055f4699fb97da37b033cfd306e7164c74fec9795d152b  pcc/py_runtime/src/py_gc_backend.c
18be143968d121913bf235b909545a0d5192fab7ccf0cf0345bde754416927d1  pcc/py_runtime/py/py_gc_backend.py
f0404504ffea9a4d8e2bdc0d2e07ae21d569925badabe9ac9e67584818c3d737  pcc/py_runtime/py/freestanding_gc_state.py
4857cc66e3be6fd1b1dc8843f9bbe85ae5bfd8380f0188bd363a6c5604478208  pcc/py_runtime/py/freestanding_gc_relocation_selector.py
ea0f2162814a863832f8f8bfa3df6ed16640fa3eb4547ec999562515069f5c74  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
4b2cf857ff69d49d3200f313aea518007cdf48ee29197b08a006644744630cda  pcc/py_frontend/codegen/runtime_abi.py
4fbe9567ca3ccee99a179b9f97879e52d59341af05b3554e87102f3cd0278511  tests/python/test_gc_backend4_production.py
ee8b56f0f1702c4fb3a2c24bba2408560db813b2eec0e1053c65cdc857f3a0f8  build/gc4-a3b-reseed-count-final.log
6c81e3deb6a23a51b427b43a3c5b96db2b88a6b5ef7b08784bf82150ab837c15  build/gc4-a3b-reseed-count-closures-final.log
268c2afb5effcb56bfabc657dc76efcb4de008e3b257442981c1c7bd7533cd3f  build/gc4-a3b-reseed-count-relocation-parity.log
```

## Next boundary

Bound or split reseed's relocation aggregate and page rebuild/commit scans.
Any page pointer retained across unlock needs an explicit lifetime mechanism;
the count cursor/revision alone does not provide one. Preserve the proven plan
growth, OOM and cursor-invalidation behavior. Do not connect A3c yet.
