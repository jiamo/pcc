# Freestanding pcc-Python allocator evidence — 2026-08-02

## Claim boundary

This slice implements the malloc-family ABI in strict freestanding
pcc-Python, selects it in the default pcc-Python runtime archive, and proves
LLVM-backed and self-backed behavior on Darwin arm64.  Darwin retains only the
explicitly named libSystem `mmap`/`munmap` page-provider ABI.  A generated
Linux x86_64 self-backend object is statically proven to use raw syscalls and
no `mmap`/`munmap` or malloc-family import.

This is `DONE_WEAK`, not the final allocator claim.  Minutes-scale RSS/pause/
throughput comparison, a real Linux executable/Docker proof, and a fresh
five-GC pcc1→pcc2→pcc3 fixed-point run remain acceptance boundaries.

## Implementation

- `pcc/py_runtime/py/freestanding_allocator.py` exports `malloc`, `calloc`,
  `realloc`, `free`, `aligned_alloc`, `memalign`, `posix_memalign`, and
  `malloc_usable_size` from pcc-Python source.
- Eight small-object size classes (16 through 2048 bytes) reuse 64 KiB slabs
  behind a freestanding atomic byte lock.  Large and over-aligned allocations
  directly own page mappings.  No C allocator implementation is linked.
- A 48-byte in-band header records mapping base/extent, requested size, usable
  size and alignment.  `realloc` preserves the old allocation on failure and
  copies only its requested prefix when it moves.
- Raw `pcc.unsafe` additions cover page allocation/free, typed pointer
  difference, signed-i64 multiplication overflow, wrapping multiplication,
  and compile-time i64 globals.  The normal Python `int` contract is not
  weakened; machine wrapping is explicitly named at the unsafe boundary.
- Exported mapped/live-requested/live-usable counters make allocator
  fragmentation and steady-state ownership measurable without libc heap APIs.
- `pcc/py_runtime/Makefile` archives
  `build_py/freestanding_allocator.o` into `libpy_runtime_pcc_py.a`.

## Focused evidence

```text
tests/python/test_freestanding_allocator.py + tests/python/test_unsafe_pages.py
9 passed in 3.97s

tests/python/test_freestanding_module.py
tests/python/test_freestanding_mem_str.py
tests/python/test_freestanding_allocator.py
tests/python/test_unsafe_pages.py
tests/python/test_unsafe_atomics.py
48 passed in 11.89s

tests/python/test_native_int_str_pcc_py_runtime.py
1 passed in 42.72s

default pcc-Python runtime, self backend, PCC_GC_BACKEND=0..4
1 passed in 42.31s (cold archive rebuild)

tests/python/test_libc_import_baseline.py
2 passed, 2 deselected in 0.35s
```

The allocator-specific suite proves:

- LLVM and self objects export the complete ABI and have no managed-runtime,
  GC, libpython, or malloc-family dependency.
- Zero-size, overflow, alignment, `calloc` clearing, `realloc` growth/shrink,
  content preservation and POSIX error behavior pass through a C ABI harness.
- 4096 steady allocate/free rounds add at most one slab; live-requested and
  live-usable counters return to zero.
- A 4096-live-allocation size-class matrix keeps telemetry exact and bounds
  mapped overhead; all allocations are freed back to their slabs.
- Four native threads perform 20,000 mixed malloc/realloc/free operations
  successfully through both LLVM and self objects.
- The default runtime binary defines its own `malloc` symbol and produces the
  same output under GC0..4.
- The Darwin import ratchets shrink from 64→62 (threads off) and 70→68
  (threads on): `calloc/free/malloc/realloc` disappear, while the explicitly
  argued `mmap/munmap` platform ABI appears.

## Remaining boundary

Record bounded long-run allocator-vs-host throughput, peak RSS, retained slab
capacity and pause deltas; exercise all five collectors on representative
long-running workloads; run the Linux x86_64 executable gate; then run one
fresh final self-host/fixed-point chain.  These measurements may require
further slab policy tuning and must not be replaced by looser thresholds.
