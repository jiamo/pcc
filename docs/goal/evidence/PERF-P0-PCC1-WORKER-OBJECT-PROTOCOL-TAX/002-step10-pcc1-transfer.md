# 002 — Step 10 transferred to pcc1; representative impact is sub-1%

Date: 2026-09-04

## Boundary

This receipt transfers the already host-green generic-binop pin deferral and
for-target ownership-transfer store through a fresh pcc1, then replays the two
retained Stage2-v4 workers used by the preceding cost wave. It is a focused
worker result, not a Stage2 or fixed-point claim.

## Focused gate

```text
tests/python/test_generic_binop_pins_only_around_slow_calls.py
tests/python/test_py_for_target_representation_join.py
tests/python/test_native_dyn_tagged_int_binop.py
tests/python/test_exact_int_loop_protocol_ratchet.py

23 passed in 175.38s
```

The packet includes GC0..4 semantics and the pin/root IR ratchets.

## Stage1 v12

Frozen source `/private/tmp/pcc-worker-protocol-source-v12.dBYVuB`, guarded by
`run_process_tree_sample.py` at 8 GiB/600s; inner Stage1 timeout 520s and
self-backend jobs 2.

```text
status COMPLETE, rc 0
sampler elapsed       179.02s
stage build wall      165.88s
tree CPU              808.70s
peak tree RSS         4,876,091,392 bytes
pcc1 sha256           939b761e27e8a9e26f89f94feb56e8759448ae6e1e8c00de1121fc3364f2b771
linkage               libSystem only; no libpython/LLVM
function canary       42
```

Receipts:
`build/worker-protocol-stage1-v12-process.result.json` and
`build/worker-protocol-stage1-v12/stage1-result.json`.

## Recorded-worker replay

`scripts/replay_pcc_codegen_worker.py` now preserves this previously temporary
procedure: it verifies a `codegen_worker.v4` manifest, rewrites only owned
result/artifact paths, restores the original Stage2 environment, records
identities and execs through `/usr/bin/time -lp`. Its focused test passes 1/1;
both replays ran under the performance lock and an 8 GiB process-tree guard.

```text
worker                    v11 instructions   v12 instructions   change
cli_bootstrap             ~684.7 B           679.064 B          about -0.8%
exception_lowering        ~203.6 B           203.541 B          about -0.03%

cli_bootstrap v12         60.10s, 6.63 GiB, rc 0, ASM output
exception_lowering v12    18.49s, 3.15 GiB, rc 0, PCO output
```

The walls are not accepted as regressions or wins from one noisy run; the
deterministic instruction counts show the transfer is real but small on these
compiler workloads. Step 10 is retained for its semantic/protocol reduction,
but it is not a solution to the 8.2x same-resource Stage gap.

## Next owner

Stop extending adjacent for/binop helpers. The next measured structural
families are shared by all object-heavy rows: the provenance radix walk in
every `py_incref`/`py_decref` (about 12% self time), and the separately
allocated/hash-indexed GC tracking node per container/instance. Measure the
already-landed node-pool work before changing it; then select one proposal with
a whole-owner ceiling and GC0..4 mirror obligations.

