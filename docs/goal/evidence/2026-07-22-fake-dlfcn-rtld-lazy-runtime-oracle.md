# Fake dlfcn RTLD_LAZY runtime-oracle closure

## Claim

The current-source pcc C frontend can compile `py_http.c` through the fake-libc
surface, and the immutable runtime-oracle fixture can cold-build all four
runtime archives and run both pcc-C and pcc-Python corpus comparisons. This
proves the missing fake `RTLD_LAZY` declaration and stale cache-key omission
are closed; it does not broaden HTTP, TLS, or package compatibility claims.

## Change

- Added the standard guarded `RTLD_LAZY == 1` definition to fake `dlfcn.h`.
- Added a direct C regression for the macro.
- Included `utils/fake_libc_include` in the runtime-oracle content key so a
  header change cannot reuse an older pcc-emitted archive.
- Preserved `py_http.c`, the pcc archive build, and all 28 oracle cases.

## Evidence

Pre-fix reductions:

- `py_http.c` pcc emit: 1 failed in 0.85s with
  `use of undeclared identifier 'RTLD_LAZY'`.
- complete runtime oracle: 2 passed, 26 setup errors in 46.05s; direct make
  reduced the fanout to `build_pcc/py_http.o`.

Post-fix gates:

- fake-libc/C focused suite: 19 passed in 0.65s.
- `py_http.c` pcc emit integration node: 1 passed in 1.20s.
- runtime-oracle cold content-key build and corpus: 28 passed in 117.43s.
- test-infrastructure cache guards: 18 passed in 0.45s.

All commands had explicit watchdogs. No test, package source, runtime source,
or oracle comparison was skipped or weakened.
