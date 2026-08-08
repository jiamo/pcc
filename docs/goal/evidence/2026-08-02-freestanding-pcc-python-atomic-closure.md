# Freestanding pcc-Python and atomic-port closure

Date: 2026-08-02

Task: `LIBC-P1-PRIMITIVES`

Status: implementation complete; final slow five-GC/fixed-point acceptance deferred.

## Claim

The typed-Python frontend now has an explicit
`__pcc_freestanding__ = True` library contract. A freestanding module:

- requires `--python-library --python-libpython=off`;
- permits only `pcc.extern` / `pcc.unsafe` scaffold imports;
- requires every function to have a raw `@c_abi_export` ABI;
- omits program main, module init, class init and module teardown;
- rejects classes and executable module-scope statements;
- validates every defined IR body and rejects managed-runtime references,
  exception machinery, libpython/libc/external calls, and calls outside the
  same verified freestanding definition closure;
- permits LLVM intrinsics, inline-assembly syscall intrinsics and verified
  intra-closure calls, so larger allocator/libc/GC implementations can be
  composed without introducing a managed-runtime dependency.

Both LLVM and the self backend emit relocatable objects for the focused atomic
module with zero undefined symbols (`nm -u` is empty).

The pcc-Python GC ports no longer declare or call the seven
`pcc_py_atomic_*` C helpers. Their former semantics are expressed directly via
ordering-explicit `pcc.unsafe` intrinsics:

- i32 load/store/add-fetch: relaxed;
- i64 load: acquire;
- i64 store: release;
- i64 add-fetch: acq_rel;
- decrement-if-positive: acquire load plus acq_rel/acquire strong CAS loop.

The seven helper definitions were removed from
`py_runtime_high_substrate.c` and their public declarations were removed from
`py_runtime.h`. Generated port IR contains `load atomic`, `store atomic`,
`atomicrmw` and `cmpxchg`, with no `@pcc_py_atomic_*` reference.

## Focused evidence

- `tests/python/test_freestanding_module.py`: 13 passed. This includes LLVM
  and self-backend object emission, zero-undefined-symbol audits, verified
  intra-closure composition, and fail-closed directive/import/class/heap/
  managed-runtime/external-call cases.
- `tests/python/test_atomic_mirror_gap.py`,
  `tests/python/test_unsafe_atomics.py`, and the freestanding suite together:
  35 passed. This includes real LLVM/self execution of the CAS
  decrement-if-positive algorithm.
- fallback and host-contract ratchets: 27 passed.
- adjacent GC abstraction/backend23/threading source and runtime tests:
  35 passed.
- content-addressed real pcc-Python runtime archive plus a self-backend native
  program: 1 passed (`test_native_int_str_pcc_py_runtime.py`).
- bootstrap baseline/startup-doc and fast GC/atomic gates were rerun after the
  implementation; startup state was regenerated from the validated board.

## Explicit remaining boundary

Per the task contract, a fresh full five-GC self-host/fixed-point chain is
still required before `DONE_STRONG`. It was not used as a diagnostic loop and
is deferred to one final slow acceptance run. No broad five-GC or full-suite
claim is made by this evidence.
