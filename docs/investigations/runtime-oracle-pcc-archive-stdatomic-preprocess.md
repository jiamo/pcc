# Investigation: runtime-oracle pcc archive rejects host `stdatomic.h` expansion

## Status
resolved

## Problem Description

The current-source `tests/python/test_runtime_oracle_diff.py` session fixture
fails for every parameterized case while building `libpy_runtime_pcc.a`.  This
is one shared archive-construction failure, not a separate semantic divergence
in each oracle program.  The previously resolved duplicate-weak-symbol failure
in `python-pcc-built-archive-weak-symbol-duplicates.md` is not recurring.

## Repro

```bash
gtimeout 240s env -u LC_ALL /usr/bin/make -C pcc/py_runtime \
  PCC=/Users/jiamo/my/pcc/.venv/bin/pcc libpy_runtime_pcc.a
```

Observed current-source result: exit 2 while compiling
`src/py_re_engine.c`, with `Error: <input>:1011: before 'LBRACE' ('{')`.
Exact preprocessing through `CEvaluator._system_cpp(...)` shows that source
`atomic_flag_test_and_set_explicit(&re_cache_lock, memory_order_acquire)` became
the Clang-host-header expression
`__c11_atomic_exchange(&(&re_cache_lock)->_Value, 1, memory_order_acquire)`.
Pcc's parser rejects the resulting `while (...) {` statement.  The system-cc
archive remains buildable.

## Test [CONFIRMED]

The direct make command above deterministically reproduces the failure at the
first stale pcc-C runtime object.  A focused C compatibility regression using
`#include <stdatomic.h>`, `atomic_flag`, explicit flag operations, and explicit
integer fetch/load operations is being added before the mechanism changes.

## Proposals

- No.1 Add a fake-libc C11 `stdatomic.h` mapped to pcc-supported atomic builtins [CONFIRMED]

## No.1 Add a fake-libc C11 `stdatomic.h` mapped to pcc-supported atomic builtins

### Code Change

Add `utils/fake_libc_include/stdatomic.h` so pcc's normal `-nostdinc` path does
not fall through to target-specific Clang header internals.  Model C11 atomic
typedefs as their scalar storage types and map memory orders and operations to
the already-supported GCC `__atomic_*` builtins.  The builtins, rather than
plain loads/stores, retain atomic code-generation semantics.  Normalize the one
runtime declaration from qualifier spelling `_Atomic int64_t` to the equivalent
parenthesized C11 spelling `_Atomic(int64_t)`, which pcc's preprocessing contract
already supports and tests.

### CONFIRMED

The focused C11 regression passed, and the archive advanced past preprocessing
and parsing.  That exposed two independent downstream boundaries, recorded in
`c-large-aggregate-assignment-selectiondag-blowup.md` and
`runtime-oracle-pcc-archive-atomic-thread-fence.md`; neither changes this
proposal's verdict.

## Report

Pcc now resolves `<stdatomic.h>` from its fake-libc surface and lowers the
runtime's C11 operations through supported atomic builtins.  The equivalent
`_Atomic(int64_t)` spelling keeps the declaration inside the parser's proven
contract.  The final complete runtime-oracle gate passed 28/28 after the two
separately recorded downstream fixes.
