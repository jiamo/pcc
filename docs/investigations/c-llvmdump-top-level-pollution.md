# Investigation: C llvmdump writes generated files into the caller directory

## Status

resolved

## Problem Description

The user reported that `temp.ooptimize.bcode` polluted the repository top-level
directory.  `CEvaluator.evaluate(..., llvmdump=True)` writes `temp.ir`,
`temp.ooptimize.bcode`, and `temp.bcode` with relative paths, so the artifacts
land in whichever directory invoked pcc.  Root-level `run.py` also knows these
names as generated cleanup targets, confirming that repository-root pollution
was an expected side effect rather than an isolated stale file.

## Repro

From an otherwise empty working directory, call:

```text
CEvaluator().evaluate("int main(void) { return 0; }", llvmdump=True)
```

Current behavior: the working directory gains `temp.ir`, `temp.bcode`, and,
when optimization runs, `temp.ooptimize.bcode`.

Required behavior: a boolean dump request writes under an explicit pcc cache
artifact directory, and a caller can supply an exact dump directory.  The
caller's working directory remains untouched.

## Test [CONFIRMED]

`tests/c/test_c_evaluator_dump_artifacts.py` exercises both the default
cache-owned location and an explicit path.  The old source fails the first test
because `<cache>/llvm-dumps/<pid>` does not exist; inspection of the isolated
caller directory confirms that `temp.ir`, `temp.bcode`, and
`temp.ooptimize.bcode` were written there instead.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/c/test_c_evaluator_dump_artifacts.py
1 failed in 0.49s
```

## Proposals

- No.1 Normalize llvmdump to a cache-owned or explicit artifact directory [CONFIRMED]

## No.1 Normalize llvmdump to a cache-owned or explicit artifact directory

### Code Change

`_normalize_llvm_dump_dir` now maps false to no dump, a path to that exact
artifact directory, `PCC_LLVM_DUMP_DIR` to the CLI/API override, and boolean
true to `<compile-cache>/llvm-dumps/<pid>`.  `_write_llvm_dump` owns every
single-TU and multi-TU write.  Security tests request their pytest directory
explicitly, and CLI help no longer promises ambiguous "temp files".

### CONFIRMED

The new focused packet proves boolean, explicit, and multi-TU ownership.  The
existing evaluator and both security dump consumers preserve their content and
behavior:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/c/test_c_evaluator_dump_artifacts.py tests/c/test_c_evluater.py
4 passed in 0.94s

gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/security/test_c_stack_protection.py \
  tests/security/test_c_ubsan_characterization.py
15 passed in 1.48s
```

The repository-root `temp.ir`, `temp.bcode`, and `temp.ooptimize.bcode` were
removed by exact path after the gates passed.

## Report

No ignore or cleanup-only workaround was added.  Proposal No.1 makes the dump
owner explicit while keeping `llvmdump=True` backward-compatible as an opt-in
diagnostic request.  The only behavior change is artifact location; compiled
IR, optimization selection, return values, and security dump contents remain
covered by the focused gates above.
