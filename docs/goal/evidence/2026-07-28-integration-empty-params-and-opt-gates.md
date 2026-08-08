# Integration empty parameters and LLVM opt gate conversion

Task: `TEST-P1-NO-SKIP-DOCTRINE-REMAINING-FAMILIES`

Status: `DONE_WEAK`

## Finding

The three skips reported by `pytest -m integration` were pytest-generated
`[NOTSET]` items for empty parameter sets:

1. `test_c_testsuite_runtime_returncode_matches_native[NOTSET]`
2. `test_identical_across_backends_gap[NOTSET]`
3. `test_unmet_obligation[NOTSET]`

The empty categories now define no test item. If entries are added later, the
same parameterized checks are defined and run.

A broader scan also found 45 IR-pass files with latent
`unittest.skipUnless(opt, ...)` gates. They now use collection-time
`pcc_gate`, and all upstream parity paths resolve an LLVM `opt` whose version
matches llvmlite. On this machine llvmlite reports LLVM 20.1.8 and resolution
selects `/opt/homebrew/opt/llvm@20/bin/opt`.

## Gates

- integration collection hook over all selected items:
  - `PCC_SELECTED_SKIP_MECHANISMS 0`
  - 4555 selected items after removing the three empty placeholders
- focused empty-parameter collection:
  - `546/571 tests collected (25 deselected)`, no `[NOTSET]`
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/c/test_ir_passes_parity.py tests/test_dependency_verdict.py`
  - `22 passed in 0.33s`
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/c/test_ir_passes_*.py`
  - `1115 passed, 60 subtests passed in 16.81s`
- repository skip-pattern scan:
  - no executable pytest/unittest skip mechanism remains; hits are source-text
    fixtures, compiler recognition strings, comments, or assertions about the
    forbidden pattern.

## Open boundary

The full default and full integration suites were not run to final summaries
in this slice. `DONE_STRONG` still requires both runs to finish with zero
skipped tests; collection-only and focused gates do not prove runtime success
for all 14,169 collected tests.
