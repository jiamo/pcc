# Investigation: BOC speedup proofs collapse under xdist CPU contention

## Status

active

## Problem Description

The default six-worker non-integration suite produced correct native BOC
results but failed both wall-clock parallelism floors:

```text
ring: 1.48x, required >= 1.5x
bank: 2.33x, required >= 2.5x
```

Both modules are independent xdist work units and each speedup proof launches a
four-thread native binary. This investigation owns benchmark scheduling and
measurement isolation. It does not assume a pcc threading semantic regression
and does not authorize lowering either speedup threshold.

This follows the general heavy-lane finding in
[`nonintegration-heavy-xdist-lane-oversubscription.md`](nonintegration-heavy-xdist-lane-oversubscription.md),
but owns the two CPU-saturating BOC proofs separately.

## Repro

Run the two public speedup proofs through two loadgroup workers so their
unmarked modules are eligible to execute concurrently:

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n 2 --dist=loadgroup \
  tests/python/test_boc_benchmarks.py::test_boc_ring_correctness_and_speedup \
  tests/python/test_boc_threading_proof.py::test_pcc_threads_give_real_parallel_speedup
```

Compare with the same nodes under `-n0`.

## Test [CONFIRMED]

The two nodes passed once while independently scheduled through two workers:

```text
2 passed in 53.73s
```

That non-deterministic GREEN does not remove the scheduling defect. The same
nodes passed sequentially under `-n0` (`2 passed in 67.65s`), with the bank
proof measuring `2.89x`, above its unchanged `2.5x` floor.

The deterministic scheduling contract
`test_boc_speedup_proofs_share_one_measured_performance_lane` failed before the
proposal:

```text
AttributeError: module 'tests.python.test_boc_benchmarks'
has no attribute 'pytestmark'
```

Thus loadgroup sees the modules as independent work units and may overlap their
four-thread binaries, exactly as the default-suite symptom requires.

## Proposals

- No.1 Put all BOC native-thread benchmarks on the existing LLVM-heavy lane
  [DENIED — incomplete]
- No.2 Lower the speedup floors [DENIED]
- No.3 Change the pcc threading runtime before isolating measurement
  [DENIED]
- No.4 Separate default correctness from explicit quiet-host speedup proof
  [pending]

## No.1 Put all BOC native-thread benchmarks on the existing LLVM-heavy lane

### Code Change

Give both modules `xdist_group(name="pcc_heavy_llvm")` and retain the structural
scheduling regression, existing benchmark programs, run counts, correctness
checks, and thresholds. Reusing the established frontend-shaped lane avoids
creating a third compiler-heavy unit that could run beside both existing heavy
lanes.

### DENIED — incomplete

The marker correctly put both speedup nodes on `gw0`, and the two focused nodes
passed (`2 passed in 59.09s`). It did not make wall-clock measurement valid
against unrelated host load. With only the two marked BOC modules collected,
ring fell to `0.67x` and the run ended `1 failed, 3 passed in 222.41s`.

At that boundary, three unrelated Python search processes consumed
approximately 674%, 95%, and 80% CPU. The host exposes 8 performance cores and
4 efficiency cores. The other five pytest workers were idle, so no xdist marker
can reserve the physical machine from external workloads.

## No.2 Lower the speedup floors

### Code Change

None.

### DENIED

The observed results are correct-but-contended and narrowly below the existing
floors. Lowering the floors would weaken the proof that pcc native threads
execute in parallel.

## No.3 Change the pcc threading runtime before isolating measurement

### Code Change

None.

### DENIED

Both failures occurred only in the broad concurrent suite and retained their
correctness markers. Runtime changes require an isolated semantic or
performance regression first.

## No.4 Separate default correctness from explicit quiet-host speedup proof

### Code Change

Keep real compiled ring/bank correctness tests in default collection. Gate only
the wall-clock serial/parallel comparisons behind
`pcc_gate(env="PCC_RUN_BOC_SPEEDUP")`. Retain the shared LLVM-heavy group for
the explicit proof, and retain the 1.5x/2.5x floors unchanged.

### pending

The deterministic contract passed (`1 passed in 0.03s`), the default native
correctness nodes passed (`2 passed in 5.37s`), all infrastructure contracts
passed (`20 passed in 0.95s`), and both complete BOC modules passed through the
normal six-worker scheduler with the wall-clock proofs deselected
(`4 passed in 7.23s`).

The explicit performance gate was attempted after the first external jobs
exited. Ring passed, but bank measured only `1.44x`
(`serial=13.37s`, `parallel=9.26s`) after the host's sustained saturation and
while the external workflow continued launching CPU searches. This is not
quiet-host performance evidence and leaves the proposal open.

The exact default suite spent roughly four minutes provisioning stage1, reached
70%, emitted one unrelated failure marker without a traceback, and hit its
unchanged 1200-second watchdog while two external search processes each used
about one CPU. It produced no final pytest summary and is not green evidence.
The watchdog left no pytest, bootstrap, pcc, pcc1, pcc2, or pcc3 child.

## Update 2026-07-29 — external saturation identified

The failed BOC-only six-worker run was not evidence of xdist overlap: all four
items shared one worker and the other workers were idle. A process snapshot
showed unrelated `tools/solve_l4_jump_schedule.py` and
`tools/search_hybrid_phase_roles.py` jobs consuming about 850% CPU in aggregate.
They belong to another workflow and were not terminated. This converts the
remaining boundary from scheduler grouping to honest performance-gate
classification.
