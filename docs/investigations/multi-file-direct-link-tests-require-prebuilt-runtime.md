# Investigation: direct-link tests require an unprepared mutable C runtime

## Status
resolved

## Problem Description
Current compiler qualification in a clean, isolated checkout fails before the
self linker: four direct-indexed/deferred-link tests hardcode a repository-local
`pcc/py_runtime/libpy_runtime.a`. That generated archive is neither a source
input nor prepared by those tests. The preceding default compiles correctly
build the pcc-Python archive, which does not satisfy this separate C-oracle path.

## Repro
The clean checkout has no `pcc/py_runtime/libpy_runtime.a`. Run:

```sh
gtimeout 120s env -u LC_ALL uv run pytest -vv -x --tb=short -n0 tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_multi_compile_deferred_direct_link_runs
```

## Test [CONFIRMED]
The first failure in `build/correctness-20260906-a/focused-02.stdout.log` is that
exact node: `explicit runtime archive not found`. The complete run stopped
with 1 failed, 47 passed, 4 deselected in 128.03 seconds. Its incremental
`focused-02.pytest.jsonl` retains the traceback before session completion.
This is a test provisioning defect; it does not establish a compiler semantic
failure. No existing matching investigation in INDEX owns this fixture issue.

## Proposals
- No.1 Use the existing immutable C-runtime test fixture [CONFIRMED]

## No.1 Use the existing immutable C-runtime test fixture
### Code Change
All four owning tests now obtain `libpy_runtime.a` from `cached_c_runtime()`.
The content-addressed fixture builds or validates its own complete archive
under a lock. The tested C runtime and direct/deferred self-link modes remain
the same. Assertions on native execution, direct-object counters and link
handoff are preserved. No production compiler source changes.

### CONFIRMED
The original failing node and all four affected execution shapes pass from the
same isolated checkout: `4 passed in 10.01s`, exit 0, recorded in
`build/correctness-20260906-a/direct-link-fix-01.stdout.log` and its incremental
JSONL report. The mutable repository-local C archive remains absent.

## Report
The existing content-addressed fixture removes the hidden dependency on a
previous local C-runtime build. All four compiled artifacts run and retain their
original expected output. Compiler source is unchanged; full compiler and
release qualification continues separately.
