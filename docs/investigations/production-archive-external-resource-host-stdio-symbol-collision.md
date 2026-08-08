# Investigation: production archive external-resource host stdio symbol collision

## Status

resolved

## Problem Description

The strict freestanding external-resource object passed its standalone LLVM,
self, C-oracle, thread, and Metal tests, but the same behavior harness crashed
before producing output when linked against the complete production
`libpy_runtime_pcc_py.a` archive.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_external_resource.py::test_built_production_archive_attributes_external_resource_to_python
```

Before the test correction the executable exited `-11`.  LLDB stopped in
`pthread_mutex_lock`, called by host `setvbuf` from harness `main`.

## Test [CONFIRMED]

The failure was observed after the archive member and `nm -A` ownership checks
had succeeded.  The standalone object used the same behavior harness and
passed, isolating the difference to complete-archive link ownership.

## Proposals

- No.1 Remove host stdio from the complete-archive harness [CONFIRMED]
- No.2 Change production archive symbol ownership for host harnesses [DENIED]

## No.1 Remove host stdio from the complete-archive harness

### Code Change

Compile the production-archive variant with `PCC_HARNESS_NO_STDIO`.  It retains
all state-machine, callback-reentry, concurrent-release, failure-metric, and
Metal dynamic-loader assertions through exit status, while the standalone
object/C-oracle tests retain detailed stdout comparison.

### CONFIRMED

The production archive intentionally supplies freestanding `stdin`, `stdout`,
`stderr`, allocation, memory/string, and stdio ABI symbols.  `nm` proved the
linked executable's `stdout` came from `freestanding_stdio.o`.  Passing that
pcc ABI object to host libSystem `setbuf`/`setvbuf` is invalid; LLDB showed the
host function dereferencing address `0x8`.  With host stdio excluded, the real
archive behavior gate passes in 0.73 seconds.

## No.2 Change production archive symbol ownership for host harnesses

### Code Change

Rename, hide, or conditionally omit the freestanding stdio symbols so an
ordinary host-C harness can use libSystem stdio while linking the archive.

### DENIED

That would weaken the archive's production no-libc ownership contract to serve
an invalid mixed-ABI test.  Host-C stdout formatting is already covered in the
standalone object differential; the production gate should respect the
archive's ABI rather than change it.

## Report

The runtime implementation was not the cause.  The test crossed two distinct
stdio ABIs in one process.  The accepted quiet harness keeps the full behavior
claim and directly tests the production archive without importing host
`FILE *` semantics.
