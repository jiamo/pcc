# SDK-struct kernel helpers: last two cc-only runtime sources removed

Date: 2026-08-01

Task: `LIBC-P2-SDK-STRUCT-HELPERS`

## Source identity

Tree of `2026-08-01-pcc-runtime-archive-pure-c-chain.md` plus this slice.

## Option chosen (with evidence): SDK-locked fake declarations

The row offered three options; the first is implemented. The macOS SDK
surfaces the two sources read are now declared in the fake libc headers,
with every layout value taken from a cc-compiled SDK oracle and locked by
a field-for-field regression:

- `utils/fake_libc_include/mach/mach.h` (new): `mach_task_basic_info`
  (size 48, resident_size +8), `MACH_TASK_BASIC_INFO(_COUNT)` = 20/12,
  `KERN_SUCCESS`, `task_info`, `mach_task_self_`.
- `utils/fake_libc_include/malloc/malloc.h` (new): `malloc_statistics_t`
  (size 32; size_in_use +8, max_size_in_use +16, size_allocated +24),
  `malloc_zone_statistics`.
- `utils/fake_libc_include/sys/resource.h`: `struct rusage` (size 144,
  ru_maxrss +32), `RUSAGE_SELF`, `getrusage`; `sys/time.h` gained a
  standard include guard so resource.h can include it.
- `pcc/py_runtime/Makefile`: the `$(CC)`-only rules for
  `build_pcc/py_os_rss.o` / `build_pcc/py_os_heap.o` are deleted — the
  generic `$(PCC) --emit-obj` rule now emits them.
- `tests/python/test_py_runtime_pcc_emit.py`: `_CC_ONLY_KERNEL_SOURCES`
  is empty.

New regression `tests/python/test_sdk_struct_helpers_pcc.py`: compiles one
probe with cc and with pcc (`pcc.api.build(kind="exe")`), asserts the two
outputs are IDENTICAL for every sizeof/offsetof/constant, and asserts the
real queries succeed from the pcc-compiled binary (task_info RSS > 1 MB,
malloc_zone_statistics in-use > 0, getrusage peak > 1 MB).

## Commands and results

```text
tests/python/test_sdk_struct_helpers_pcc.py            1 passed
sensitive C gates (parser, lz4, unsigned, lua pair)     66 passed
gtimeout 300s ... -m integration tests/python/test_py_runtime_pcc_emit.py
                                                        87 passed in 36.08s
pure-C chain re-proven with the 86/86 archive:
  stage1 (barrier smoke) S1=0; stage2 S2=0; stage3 S3=0
  pcc2/pcc3 metadata-normalized byte-identical
tests/python/test_bootstrap_gate_baseline.py            4 deselected
  (integration-marked; the default-chain fixed point was re-proven
   directly the same night)
```

## Supported claim

No runtime C source needs the host cc anymore: pcc emits every runtime
object, the per-file emit gate passes with an empty exception set, and the
all-pcc-emitted archive still carries the full pcc1→pcc2→pcc3 chain to the
normalized fixed point (darwin-arm64, self backend, no-libpython).

## Not proven

- The linux branches of both helpers remain untested until a gated linux
  host exists (their own comment already records this).
- The peripheral cc-rule files outside the 86-source set
  (Metal/dlpack/gc-external/waitset) are platform-runtime integration
  surfaces, not SDK-struct readers, and stay under their own rows.
