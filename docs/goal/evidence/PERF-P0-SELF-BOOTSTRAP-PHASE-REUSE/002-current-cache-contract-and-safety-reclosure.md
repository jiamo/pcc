# 002 — current cache contract and memory-safety reclosure

Date: 2026-09-04

## Claim

The compiler-level phase-reuse mechanism remains current and the memory-safety
condition that reopened this row is now closed. Frontend IR and self-backend
objects use content-addressed identities across equivalent invocations; cache
corruption fails closed; ordinary bootstrap shares the namespaces; and a
current cold Stage2 has completed below the 8 GiB process-tree ceiling.

This does not claim that the current cold Stage2 is fast. It is 1350 seconds,
so the remaining Stage2 <= Stage1 gap stays assigned to the native data-plane
emit/per-operation owners. Fresh final-source Stage3, GC1--4, and broad suites
remain explicit downstream correctness work under the human-selected
performance-first order.

## Existing measured reuse result

The retained same-source receipt
`2026-07-31-bootstrap-phase-reuse-cold-baseline-and-cache-wiring.md` records:

```text
cold pcc1 -> pcc2       438.60s   5.93 GB
equivalent warm repeat   29.87s   2.15 GB
dominant emit phase     374.9s -> 11.6s
output                  byte-identical pcc2, SHA a4b01b30...72b0
```

The repeat is 93.2% faster than the cold stage and remains below the 8 GiB
ceiling. The result predates the current compiler source but proves the
implemented reuse mechanism's performance claim; current focused tests below
prove that its keys and failure behavior have not drifted.

## Current cold/safety baseline

Frozen v18 Stage2 (`build/inline-edge-stage2-capped-v4`) supplies the current
cold owner and safety receipt:

```text
return code / output       0 / runnable libSystem-only pcc2
wall / tree CPU            1349.675s / 3037.835s
process-tree peak          7,812,333,568 B (< 8 GiB)
modules / object misses    224 / 224 (cache explicitly off)
frontend+summary window    about 150s before deferred emit
emit worker wall sum       about 2540s
owned link                 86.374s
sampler table retries      0
```

The lane receipt names one serial, six paired-oversized, eight heavy, sixteen
medium, and 193 small workers. It is the cold comparison point; it is not
compared to the historical warm run as though their source or scheduler were
identical.

## Current focused gates

The current tree passes the cache/identity/determinism packet:

```text
17 passed, 104 deselected in 2.77s
```

Files:

- `test_py_frontend_ir_pass_pipeline.py`
- `test_bootstrap_cache_identity_scope.py`
- `test_self_backend_cache_identity.py`
- `test_py_frontend_compile_cache.py`
- cache/profile/deterministic nodes in `test_pcc_bootstrap_full.py`

These prove that frontend keys are GC-invariant but bind compiler/source and
codegen-relevant inputs; copied compiler binaries key by bytes rather than
path; object-emitter identity is independent and backend-source-sensitive;
roundtrip is deterministic; tampering is rejected; equivalent concurrent
copies coordinate; and all bootstrap stages share one content namespace.

The adjacent bootstrap performance/resource packet is also current:

```text
7 passed, 1 deselected in 0.07s
```

`python -m pcc.bootstrap_cache_identity` produces two well-formed current
namespace identities:

```text
frontend  68d4debd704f37dd78904a301c3c245eba8813e208f27b9b46e3c541fa8e895c
backend   f290036f794eea2d39ec197b09d83f3833dc577f5543e9dad127be4b1c32e493
```

## Validation routing

Historical criterion-4 evidence records complete five-GC, non-integration and
integration summaries for the phase-reuse implementation. Those runs are not
relabelled as current-source correctness. The task board already assigns fresh
final-source checks to:

- `PERF-P0-NATIVE-DATA-PLANE-GC1-GC4-TRANSFER`;
- `PERF-P0-POST-FIVE-GC-STAGE-CONSTRAINT-CONVERGENCE`;
- `PERF-P0-PCC1-BOOTSTRAP-BEATS-HOST` for the GC0 performance/fixed-point
  source preceding them.

Running a new cold matrix here would violate the explicit native-first order
and use a broad gate as discovery. No such run was launched.

## Verdict

`[CONFIRMED]` for phase reuse and its repaired safety boundary. The row can
close; it no longer blocks the native data-plane/emit task. Cache hits remain
an incremental-development optimization and must never be compared against a
cache-miss arm for the final cold Stage2 <= Stage1 claim.
