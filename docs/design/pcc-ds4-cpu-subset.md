# pcc ds4 bounded CPU compile subset

Status: `DS4-P1-CPU-COMPILE-SUBSET` complete for the pinned finite subset.
Machine classification:
`tests/ds4/ds4_cpu_compile_subset.json`.

## Proven modes

- Host pcc C frontend -> object -> native system link/run:
  - `ds4_ssd.c`, exercised through parsing, cache planning, and zero-byte
    memory-lock lifecycle; stdout equals the same source compiled by native cc.
  - `tests/test_q4k_dot.c`, all four block-layout/scale/known-dot/50-vector
    numeric tests; stdout equals native cc.
- Host pcc C frontend -> object only:
  - complete `ds4_kvstore.c` translation unit. Runtime remains unproven because
    its public paths link against ds4 engine/session/token payload APIs outside
    this subset.

The first probes exposed generic fake-libc omissions rather than ds4 parser
special cases. `sys/mman.h` now owns platform-correct anonymous-map constants
and memory-lock declarations; `dirent.h` owns target ABI `DIR/struct dirent`
and directory functions; `time.h` owns target `CLOCK_MONOTONIC` and clock
declarations. A minimized pcc/native behavior test covers mmap, monotonic clock,
and real directory-entry name access.

## Not proven

No full ds4 engine link/model run, Metal/CUDA/ROCm/API compilation, full GGUF
quantizer conversion, KV session payload behavior, performance, or model-quality
parity is claimed. Those remain separate rows.
