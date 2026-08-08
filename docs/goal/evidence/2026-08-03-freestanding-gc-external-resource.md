# Freestanding pcc-Python GC external-resource ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

## Claim boundary

The production pcc-Python runtime archive no longer compiles or links
`pcc/py_runtime/src/pcc_gc_external_resource.c`.  Its external GPU buffer/fence
registry ABI now comes from strict
`pcc/py_runtime/py/freestanding_gc_external_resource.py`.  The C source remains
as a C-runtime implementation and differential oracle.

This slice does not complete all five collectors.  It does not yet prove a
strict freestanding closure for `py_gc_backend.py`, `py_obj_gc.py`, telemetry,
weakref/finalizer/resurrection, suspended roots, concurrent/relocating
synchronization, or the final five-GC fixed-point and long-run matrix.

## Implementation contract

The pcc-Python object preserves the C oracle's 80-byte raw node ABI and:

- records the initialized GC backend for every resource under GC0..4;
- implements retain, fence completion, pending/ready counters, and exactly-once
  release with explicit atomics;
- detaches a node and releases the registry lock before calling foreign release
  or context-free callbacks, permitting callback reentry;
- copies the Metal runtime-library path and resolves the generic release ABI
  with explicit `dlopen`/`dlsym`/`dlclose` boundaries;
- uses only raw allocation, pointer, atomic, callback, platform-loader,
  safepoint, and GC-backend-query intrinsics; it contains no managed-runtime or
  libpython calls.

The new fixed-signature `pcc.unsafe` boundaries fail closed: function addresses
must name a body in the current module, foreign calls have fixed signatures,
and the freestanding validator admits only the exact runtime/dynamic-loader
symbols exposed by imported intrinsics.  An arbitrary `extern` declaration of
`pcc_gc_backend` remains rejected.

## Differential and backend gates

```text
20 passed in 2.48s
  tests/python/test_unsafe_atomic_global_store.py
  tests/python/test_unsafe_atomics.py
  tests/python/test_unsafe_atomics_x86_64.py

14 passed in 8.84s
  tests/python/test_unsafe_runtime_boundaries.py
  tests/python/test_unsafe_atomic_global_store.py
  tests/python/test_freestanding_gc_external_resource.py

7 passed in 2.77s
  tests/python/test_freestanding_gc_index_table.py
  tests/gpu_gc/test_runtime_external_resource.py

9 passed in 1.55s
  focused AArch64 peephole, atomic behavior, and encoder regressions

23 passed, 2 deselected in 3.27s
  freestanding discipline, IR fallback baseline, and bootstrap baseline

36 passed in 5.69s
  tests/python/test_ir_scaffold_symbols.py
```

The full external-resource harness covers invalid inputs, all five backend
identities, both retain/fence orderings, idempotent fence completion, callback
reentry, context-free callbacks, release failures and metrics, four concurrent
threads with 200 resources each, and a fake Metal release dylib.  LLVM and self
objects produce the same result as the retained C oracle.

The object has exactly these undefined boundaries:

```text
calloc dlclose dlopen dlsym free malloc memcpy
pcc_gc_backend pcc_thread_safepoint strlen
```

## Self-backend correction

The first self harness exposed a general AArch64 optimizer bug: an atomic
release store was classified as defining its source register, so composed
stack-forwarding/move-store peepholes deleted the zero later consumed by
`stlr`.  The minimized constant-to-global atomic test failed on self and passed
on LLVM before the change.  Store-opcode liveness now classifies `stlr`,
`stlrb`, and `stlrh` as consumers; the minimized and full thread/Metal gates
pass.  See
`docs/investigations/self-backend-freestanding-external-resource-harness-stalls.md`.

## Production link ownership

The content-addressed real archive contains
`freestanding_gc_external_resource.o` and does not contain
`pcc_gc_external_resource.o`.  `nm -A` attributes the public registry ABI to
the pcc-Python object, and the quiet production-archive harness executes the
same full behavior assertions.  It is intentionally quiet because the complete
archive owns the freestanding `stdout` ABI; mixing that object with host
libSystem `setbuf` is invalid.  The standalone differential retains stdout
comparison.  See
`docs/investigations/production-archive-external-resource-host-stdio-symbol-collision.md`.

## Fresh pcc1 evidence

A current-source self/no-libpython stage1 completed and produced
`build/bootstrap/pcc1` (60,623,856 bytes, timestamp 2026-08-03 13:24:36).  Its
CLI smoke passed:

```text
1 passed in 0.13s
```

That pcc1 compiled the new freestanding module to LLVM/object form in 0.7
seconds.  `nm -u` reported exactly the ten explicit boundaries above, and the
object exported `pcc_gc_external_resource_register`,
`pcc_gc_external_resource_poll`, and
`pcc_gc_external_metal_buffer_register`.

## Deferred slow gate

The combined fallback-baseline command was deliberately not claimed green.  It
passed its first ten cases, then
`test_closure_per_module_codegen_passes` exceeded separate 60- and 180-second
diagnostic watchdogs while CPU-active; both runs were terminated without a
pytest summary and left no children.  This broad closure recompilation is not
needed to validate the fixed-signature freestanding boundary slice and remains
for the final slow acceptance pass.

## Remaining task boundary

The next slice is the strict dependency/ownership audit of the production
`py_gc_backend.o`, `py_obj_gc.o`, and telemetry objects.  It must split raw
collector/kernel state from any managed facade without duplicating graph
semantics, then continue through weakref/finalizer/resurrection, suspended
frames and scheduler roots, concurrent synchronization, and relocation before
the one final five-GC semantic/fixed-point/long-run matrix.
