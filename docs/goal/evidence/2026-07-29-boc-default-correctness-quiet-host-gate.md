# BOC default correctness and quiet-host performance boundary

Date: 2026-07-29

Task: `TEST-P0-BOC-PARALLELISM-CONTENTION`

## Source identity and symptom

The default six-worker non-integration suite reported correct BOC native
results but failed wall-clock floors:

```text
ring 1.48x, required >= 1.5x
bank 2.33x, required >= 2.5x
```

A reduced BOC-only run later measured ring at `0.67x` even though every BOC
item shared one xdist worker and the other five workers were idle. At that
time unrelated processes from another workflow consumed approximately 674%,
95%, and 80% CPU on a host with 8 performance and 4 efficiency cores.

## Changed behavior

- Both BOC modules share the existing `pcc_heavy_llvm` loadgroup lane.
- Default collection compiles and executes the parallel ring and bank programs,
  checking the ring invariant, `DONE`, and all four worker outputs.
- Only serial/parallel wall-clock comparisons use
  `pcc_gate(env="PCC_RUN_BOC_SPEEDUP")`.
- The 1.5x ring and 2.5x bank floors remain unchanged.

This is host pcc, LLVM-backed, no-libpython native runtime evidence. It is not a
self-backend or pcc1/pcc2/pcc3 fixed-point claim.

## Green gates

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_test_infrastructure_efficiency.py
20 passed in 0.95s

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_boc_benchmarks.py::test_boc_ring_correctness \
  tests/python/test_boc_threading_proof.py::test_pcc_threads_complete_all_workers
2 passed in 5.37s

gtimeout 180s env -u LC_ALL uv run pytest -q -n 6 --dist=loadgroup \
  tests/python/test_boc_benchmarks.py tests/python/test_boc_threading_proof.py
4 passed in 7.23s
```

The default BOC run reported no skipped tests. The two performance proofs were
deselected by the repository's `pcc_gate` collection policy, not reported as
runtime successes.

## Open evidence

The explicit gate:

```text
PCC_RUN_BOC_SPEEDUP=1 ... -n0
```

passed ring but failed bank at `1.44x` after prolonged host saturation. It is
not quiet-host performance evidence and does not justify changing the 2.5x
floor.

The exact default suite reached 70% with one unknown failure marker and then
hit its unchanged 1200-second outer watchdog while another workflow continued
CPU searches. It had no final summary and is not green. No pytest/bootstrap/pcc
children survived the watchdog.
