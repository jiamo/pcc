# BUG-P0-RUNTIME-ORACLE-ARCHIVE-BUILD closure evidence

Source identity: dirty worktree based on
`20afd0795e24c9abc178f87b3304cc1f4760a312` on macOS arm64.  This evidence is
worktree-local and is not a clean-commit or release claim.

## Changed behavior

- Pcc preprocessing owns a parser-facing fake `stdatomic.h`; it no longer
  consumes target-specific Clang `__c11_atomic_*` wrapper expansions.
- The runtime uses the supported `_Atomic(int64_t)` declaration spelling.
- Large direct addressable C aggregate assignments lower to one bounded
  `llvm.memmove` instead of thousands of aggregate SSA operations.
- GCC `__atomic_thread_fence` lowers to an LLVM fence with order-preserving
  semantics.
- Runtime-oracle archive make steps have a 300-second process-group watchdog,
  so timeout or interruption terminates descendants rather than orphaning pcc.
- `py_re_engine.c` no longer has a 1,200-second test exemption.

## Exact evidence

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_clang_compat.py::test_fake_stdatomic_header_lowers_c11_operations_to_supported_builtins \
  tests/c/test_clang_compat.py::test_large_struct_assignment_uses_bounded_aggregate_copy_ir \
  tests/c/test_clang_compat.py::test_gcc_atomic_fetch_and_lock_builtins_lower_to_llvm_atomics \
  'tests/python/test_py_runtime_pcc_emit.py::test_pcc_emits_object_for_runtime_source[py_re_engine.c]'
result: 4 passed in 6.71s

direct current-source py_re_engine.c --emit-obj
result: real 8.19s (pre-fix exceeded 240s; historical exemption ~300s)

gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  'tests/python/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_equivalence[class_basics]' \
  'tests/python/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_py_equivalence[class_basics]' \
  tests/python/test_subprocess_timeout_runtime.py
result: 4 passed, 1 skipped in 4.05s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_runtime_oracle_diff.py
result: 28 passed in 26.84s
```

## Supported claim

Current dirty-worktree cc-C, pcc-C, and pcc-Python runtime-oracle archive
construction and all corpus comparisons pass.  The specific large-aggregate
SelectionDAG blow-up is removed without weakening aggregate-copy semantics.

## Not proven

This is not the requested final full-suite/integration result, not a clean
commit result, and not a five-GC bootstrap claim.  The separately reported GC
effectiveness failures remain owned by their task-board cards.
