# ds4 bounded CPU compile subset — 2026-07-17

Task: `DS4-P1-CPU-COMPILE-SUBSET`.

## Claim and causality

Against pinned ds4 commit
`80ebbc396aee40eedc1d829222f3362d10fa4c6c`, pcc compiles and executes the
standalone SSD/POSIX and Q4_K numeric units with byte-identical stdout to native
cc. It also compiles the complete KV-store translation unit to an object; that
is explicitly classified runtime-unproven because the ds4 engine/session/token
closure is outside this slice.

The initial SSD failure was `MAP_ANON` undeclared and the initial KV failures
were missing `DIR` followed by missing `CLOCK_MONOTONIC`. All were fake-libc
surface gaps. The reusable fixes add target-correct mmap constants/lock APIs,
target ABI dirent layout/functions, and monotonic clock constants/APIs. A
minimal pcc/native runtime probe validates mmap writes, `clock_gettime`, and
`readdir(...)->d_name` against the host ABI.

## Gate

- Machine-readable classification/source-hash contract plus four behavior or
  compile tests: `5 passed in 3.83s`.
- SSD oracle: `gib=17179869184 experts=37 plan=800/600/4/400` under pcc and
  native cc.
- Q4_K oracle: block layouts, scale extraction, known dot, and 50 generated
  blocks all pass under both compilers (`4/4 tests passed`).
- KV-store: non-empty pcc and native objects from the complete pinned source.

No full ds4, GCC suite, GPU backend, bootstrap, or five-GC matrix was run.
