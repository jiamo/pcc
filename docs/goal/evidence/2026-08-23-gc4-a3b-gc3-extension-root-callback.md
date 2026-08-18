# GC4 A3b GC3 extension-root callback split

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b GC3 callback-holder sub-boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

Backend-3 generational promotion in C and strict pcc-Python no longer invokes
an extension module's external `PyModuleDef.m_traverse` callback while holding
the GC graph lock. The scheduler unlocks and finishes remembered/TLS cleanup,
then calls `pcc_capi_visit_extension_module_state_roots`. Each managed root
reported by the external traversal enters a small runtime-owned callback that
reacquires the graph lock only around `promote_young_object` / strict
`promote_young_if_known`, then unlocks before returning to extension code.

This claim is limited to the GC3 generational-promotion call site. Trace-cycle
root seeding still calls extension traversal through
`pcc_gc_gray_current_roots` while graph-locked, and
`pcc_gc_visit_runtime_roots` still invokes caller and extension visitors under
that lock. Registered frame/scheduler walks, owner-referent visitors, and
other unbounded/allocator-capable promotion holders also remain. A3c is not
connected.

## Genuine RED

The new C/strict source-order contract failed before production edits because
strict had no extension-root promotion callback owner:

```text
IndexError: list index out of range
1 failed in 0.10s
```

The first dynamic harness included both public `Python.h` and private
`py_internal.h`; duplicate object-layout typedefs made the probe fail to
compile. That harness shape is [DENIED]. The final probe includes only public
`Python.h` and declares the narrow thread/diagnostic ABI it needs.

## Implementation

- C `pcc_gc_promote_extension_module_state_root` now validates the reported
  root and owns one graph-lock acquire/promote/release transaction.
- C `pcc_gc_step_generational_promotion` moved the whole external extension
  traversal after graph unlock, detached remembered-node finish and deferred
  TLS decref.
- Strict scheduler now exports the matching
  `pcc_gc_generational_promote_extension_module_state_root`, passes its address
  to the pcc-Python module-state visitor after unlock, and has the exact
  `(c_ptr, c_ptr) -> c_void` cross-object visitor ABI.
- No extension callback is serialized by a no-park region; no GC or extension
  mode is weakened.

## Runtime proof

The focused probe creates a real `PyModuleDef` with `m_size`, registers it via
`PyModule_Create`, stores a managed root in its module state and starts a
contender thread. `m_traverse` wakes and joins that thread; the contender's
next operation calls `pcc_gc_object_is_known`, which acquires the same graph
lock. It then reports the state root through `Py_VISIT`, exercising the
runtime-owned per-root promotion callback.

The probe would deadlock under its 20-second subprocess watchdog if external
traversal still held the graph lock. It passes when linked independently
against the C runtime and strict pcc-Python production runtime.

## Gates

- Direct strict self/no-libpython closure emitted
  `/tmp/gc3_extension_root_scheduler.ll`.
- Scheduler source, LLVM/self exact object closure and production owner: 7/7
  passed in 1.78 seconds from the current successful archive cache; its cold
  run passed in 124.29 seconds.
- C/strict true-pthread extension traversal: 2/2 passed in 0.70 seconds.
- C/strict TLS promotion, foreign-release and remembered-overflow neighbors:
  6/6 passed in 7.49 seconds.
- Python syntax, C syntax with threads off/on and `git diff --check` passed.

The existing real extension-module integration gate was attempted for GC3 and
failed during setup before executing the GC: self-link mode reports
`pcc self-link mode does not support native-extension export anchors`. This is
a mode-labeled package/self-link capability blocker, not green integration
evidence and not evidence against the runtime callback split. The direct
production-runtime module/traverse probes above remain the scoped dynamic
proof. No fallback or export-anchor workaround was introduced.

## Frozen identities

```text
8c4d7ecc5abb4c937452a3267c9d45e59d6d865288d78060dfbdf5e9276ac4bd  pcc/py_runtime/src/py_gc_backend.c
f5f87c7284efc0c307c8011b84c4c41240de262186695ed36553d9512f20cb12  pcc/py_runtime/py/freestanding_gc_generational_scheduler.py
eb5db47432c061c0aeedc91fc2866e8d970ed1e34599a1af41774c5900a5e222  pcc/py_frontend/codegen/runtime_abi.py
fcc4177aeacf60be79288b67b517d4089e5e8be47760be96d6c1f05102a7ee20  tests/python/test_freestanding_gc_generational_scheduler.py
7b18fc017c262c9fddae4078410f67f9df76928b98622404b85e1dba06a59ee5  tests/python/test_gc_backend_generational.py
4aa45fa303f47affd4ea1f301aefd7aec18b01769c2a54b7d2bd9fa5d125d502  /tmp/gc3_extension_root_scheduler.ll
c213f0278d50f522c1e08d6bf3e1628c876fb33e674191f7d2c65f2d7add982c  build/gc3-extension-root-source-final.log
8f0fa16a5870cde3d3b24553f3816656c57fbbf4487f95a990fdbe7ff206b0d3  build/gc3-extension-root-final.log
ce692c5f4ebfedb266b527835e7858199378681b66105b32fac17754abaeed91  build/gc3-extension-root-neighbors.log
0c31456eddd57d7fb7a285ed973cdfd35bbffd882bea179e8633b05fb91d1a4b  build/gc3-extension-root-integration.log
```

## Next boundary

Do not connect A3c. Split or bound registered frame/scheduler root walks,
owner-referent promotion, trace-cycle extension traversal and caller-provided
runtime-root visitors, along with remaining allocation/tripwire/log holders.
