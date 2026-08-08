# BOC quiet-host speedup and suite-summary closure

Date: 2026-07-31

Task: `TEST-P0-BOC-PARALLELISM-CONTENTION`

## Source identity

Base commit `98e62890963c60515d6f8ddc8c31996b04500f95` plus this session's
package-profile and bootstrap-cache-wiring slices (host-side tooling; no BOC,
scheduler, benchmark-threshold, or runtime-semantics change).

## Commands and results

Quiet host (no concurrent suite, compiler chain, or benchmark load):

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_boc_benchmarks.py::test_boc_ring_correctness \
  tests/python/test_boc_threading_proof.py::test_pcc_threads_complete_all_workers
2 passed in 4.91s

gtimeout 300s env -u LC_ALL PCC_RUN_BOC_SPEEDUP=1 uv run pytest -q -n0 \
  tests/python/test_boc_benchmarks.py::test_boc_ring_correctness_and_speedup \
  tests/python/test_boc_threading_proof.py::test_pcc_threads_give_real_parallel_speedup
2 passed in 57.49s          # both unchanged 1.5x/2.5x floors passed

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_test_infrastructure_efficiency.py
21 passed in 0.66s
```

Exact six-worker non-integration suite, final summaries earlier the same day
(default BOC correctness tests included in both selections; zero surviving
compiler/benchmark/pytest children verified after each):

```text
gtimeout 1200s env -u LC_ALL uv run pytest
9582 passed in 1125.63s     # clean 98e62890
9602 passed in 1044.91s     # 98e62890 + package-profile slice
```

The later cache-wiring slice touches only `scripts/bootstrap.sh`,
`pcc/bootstrap_cache_identity.py` (new), and the pytest bootstrap helper's
identity delegation — none of which participate in BOC collection,
scheduling, or thresholds.

## Exit criteria mapping

1. Ring/bank markers and the 1.5x/2.5x floors are unchanged (asserted by the
   infrastructure contracts; thresholds passed unmodified).
2. Default collection retains compiled no-libpython ring/bank correctness
   without a wall-clock claim (default gate, 2 passed).
3. `PCC_RUN_BOC_SPEEDUP=1` selects both wall-clock proofs on one heavy lane
   and passes on a genuinely quiet current multicore host (2 passed in
   57.49s; the prior 1.44x bank miss reproduced only under sustained external
   saturation).
4. The exact six-worker suite completed twice today with final summaries and
   no surviving children.

## Not proven

- Wall-clock guarantees under contended hosts (explicitly out of scope by
  design: the speedup proofs are opt-in and quiet-host-labeled).
- The 900-second suite target, which belongs to
  `PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE`.
