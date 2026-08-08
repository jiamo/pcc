# Investigation: integration empty parameters and latent LLVM opt skips

## Status

resolved

## Problem Description

`uv run pytest -m integration` reported three skipped tests even though direct
searches initially appeared to show no `pytest.skip` calls in integration
tests. Separately, the IR parity family retained unittest skip decorators whose
availability checks selected an arbitrary PATH `opt`.

## Repro [CONFIRMED]

A collection hook inspected selected integration items for pytest skip markers
and unittest skip attributes. It identified exactly three generated items:

```text
tests/c/test_c_testsuite.py::test_c_testsuite_runtime_returncode_matches_native[NOTSET]
tests/python/test_intent_constraints.py::TestObligation6GCEquality::test_identical_across_backends_gap[NOTSET]
tests/python/test_intent_constraints.py::TestIntentGaps::test_unmet_obligation[NOTSET]
```

All three carried pytest's reason `got empty parameter set`. They came from
empty manifest/gap lists, not explicit skip calls.

The repository scan also found 45 `unittest.skipUnless` decorators in IR-pass
tests. Those do not affect the integration selection, but would report skips
in the default suite on a machine without `opt`.

## Resolution

- Empty optional parameter categories conditionally define their parameterized
  test only when the category contains entries.
- IR-pass availability uses `pytest.mark.pcc_gate`, which deselects unavailable
  dependencies at collection rather than reporting a passed suite with skips.
- `find_opt_binary()` provides the single version-matched LLVM tool decision;
  the parity harness uses it by default, and direct `opt` subprocess tests use
  the same resolved path.
- Lower-expect retains its structured dependency-verdict contract with a
  resolver backed by `find_opt_binary()`.

## Test [CONFIRMED]

The selected integration collection now reports
`PCC_SELECTED_SKIP_MECHANISMS 0`. The complete IR-pass family reports
`1115 passed, 60 subtests passed`, and the parity/dependency focused gate
reports `22 passed`.

## Report

The original three integration skips are removed, and no executable
pytest/unittest skip mechanism remains in `tests/` or `pcc/`. Full default and
integration runtime summaries remain the task-board boundary for a
`DONE_STRONG` repository-wide zero-skip claim.
