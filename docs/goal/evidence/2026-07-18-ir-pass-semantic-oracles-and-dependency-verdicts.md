# IR-pass behavior oracles (4 families) + structured dependency/platform verdicts

## Claim

1. The instsimplify, loop-rotate, mem2reg, and inline pass families each have
   an independent BEHAVIOR oracle: the same function is executed (llvmlite
   MCJIT) before the pass, after pcc's pass, and after an independent
   `opt -passes=<pass>` run, over a bounded input matrix; result equality is
   the claim and each file carries an AST guard test preventing regression to
   IR-substring assertions. Modeled on the existing lower-expect oracle.
2. Dependency/platform prerequisites in claim-bearing test families are
   structured verdicts (pcc.dependency_verdict), not bare skips:
   system-cc (vthread timer mirror, alternatives cc|clang|gcc), prebuilt
   runtime archive (runtime_substrate_spike), csmith generator (availability
   / generated-case execution / semantic parity kept distinct; tool identity
   recorded via record_property when present).

## Changes

- pcc/dependency_verdict.py: +probe_first_executable_dependency (ordered
  alternatives), +probe_artifact_dependency (on-disk artifact),
  +probe_platform_capability (platform/OS capability, never feature proof).
- tests/test_dependency_verdict.py: +4 unit tests for the new probes.
- tests/c/test_ir_passes_{instsimplify,loop_rotate,mem2reg,inline}_semantic_oracle.py (new).
  Fixtures: identity-simplification chain f(x)=x; single-step counting loop
  f(n)=max(n,0) across zero/one/many iterations; branch-merged stack slot
  f(x)=x<0?-x:2x; internal callee f(x)=2x+1 with int32-wrap matrix values.
- tests/vthread/test_timer_heap_mirror.py: CC_VERDICT (first-of cc|clang|gcc).
- tests/python/test_runtime_substrate_spike.py: runtime-archive artifact verdict.
- tests/c/test_csmith.py: CSMITH_VERDICT + headers verdict-style reason +
  test_csmith_tool_identity_recorded_when_present (record_property: path,
  version, include dir).

## Verification [CONFIRMED]

- 4 oracle files + their sibling real/structural suites: 138 passed.
- tests/test_dependency_verdict.py: 7 passed.
- timer mirror + dependency_verdict: 23 passed (cc present; C parity hard).
- runtime_substrate_spike: 40 passed.
- csmith: 18 passed, 3 skipped (native compile/run rc=124 timeouts —
  execution-layer skips with csmith PRESENT; availability verdict AVAILABLE).

