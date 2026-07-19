# Investigation: rebuilt pcc runtime archive leaves `__atomic_thread_fence` unresolved

## Status
resolved

## Problem Description

Once `libpy_runtime_pcc.a` could rebuild from current source, the pcc-C runtime
oracle failed at link time.  `pcc_threads.o` referenced an external
`__atomic_thread_fence`, proving that pcc recognized other GCC atomic builtins
but did not lower this fence builtin to LLVM IR.  A stale archive had hidden
the missing current-source compiler surface.

## Repro

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  'tests/python/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_equivalence[class_basics]'
```

Pre-fix: clang link failed with undefined symbol `___atomic_thread_fence`,
referenced from multiple virtual-thread functions in `pcc_threads.o`.

## Test [CONFIRMED]

`test_gcc_atomic_fetch_and_lock_builtins_lower_to_llvm_atomics` was extended
with a sequentially consistent `__atomic_thread_fence`.  It reproduced the
same undefined symbol before the compiler change and passed afterward.

## Proposals

- No.1 Lower GCC thread-fence orders to LLVM fence instructions [CONFIRMED]

## No.1 Lower GCC thread-fence orders to LLVM fence instructions

### Code Change

Register `__atomic_thread_fence` as a builtin and map relaxed to no runtime
fence, consume/acquire to acquire, release to release, acq_rel to acq_rel, and
seq_cst to seq_cst.  Unknown/non-constant orders conservatively become seq_cst.

### CONFIRMED

The focused GCC atomic regression passed, a freshly emitted `pcc_threads.o`
linked into `libpy_runtime_pcc.a`, both runtime-oracle families passed their
focused class case, and the complete file passed 28/28.

## Report

The fix preserves the source fence and implements it in the compiler; it does
not remove synchronization or add a platform-specific linker workaround.
