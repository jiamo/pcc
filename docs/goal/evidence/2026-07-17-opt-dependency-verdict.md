# Structured LLVM opt dependency verdict

Date: 2026-07-17

Task: `AUD-P1-DEPENDENCY-GUARD-CLAIM-AUDIT`

## Inventory and selected family

Dependency guards outside the already-closed package/proxy/GPU-package
surfaces cluster into external optimizer tools, system compiler/linker tools,
prebuilt runtime artifacts, optional corpus generators, and manual/stress
enablement. The finite selected family is the LLVM `opt` prerequisite for the
lower-expect structural and semantic-oracle tests.

## Proven change

- `DependencyVerdict` records `AVAILABLE` or `UNAVAILABLE`, the dependency
  identity, resolved path, reason, and the mandatory false fields
  `feature_claimed` and `runtime_executed`.
- An unavailable executable produces an explicit
  `UNAVAILABLE[executable:<name>]` skip reason; absence is not a pass or feature
  claim.
- An available executable records its resolved path but still does not itself
  claim that a pass ran. The selected test's hard behavior assertions supply
  execution proof separately.
- Both lower-expect files use the structured probe. An AST source guard rejects
  regression to direct `shutil.which("opt")` or a plain `requires LLVM opt`
  skip for this family.

## Gate

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_dependency_verdict.py \
  tests/c/test_ir_passes_lower_expect_semantic_oracle.py \
  tests/c/test_ir_passes_lower_expect_real.py -rs
```

Result: `18 passed in 2.15s` (local `opt` available; no skips).

## Remaining schedulable families

Separate task-board rows cover system C compiler prerequisites, prebuilt
runtime archive prerequisites, and the optional csmith generator. Platform
guards remain in the dedicated platform-claim audit task.

## Claim boundary

This proves structured dependency classification only for the selected LLVM
`opt` lower-expect family. It does not prove that `opt` is universally present,
that an unavailable run executed any IR pass, or that the remaining dependency
guards are already classified.
