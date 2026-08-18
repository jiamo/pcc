# Investigation: imported valueclass return annotations lose aggregate ABI

## Status

resolved 2026-08-28

## Problem Description

The compiler native-data-plane def/use record uses `CompilerInt4`, defined in
`self_backend_value_arena`, as the annotated return of a method defined in
`self_backend_kernel`.  During a full no-libpython/self Stage1 build, that
annotation becomes `DynType`; the method body produces `{ i64, i64, i64, i64 }`
and the generic object marshal rejects the aggregate.

This is one boundary beyond the resolved
`cross-module-valueclass-aggregate-abi-export.md`: the valueclass is defined in
module A, imported into module B and used in B's public method annotation, then
the method is consumed from module C.  The existing two-module test defines the
valueclass and provider method in the same module.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_cross_module_valueclass_abi.py::test_imported_valueclass_return_annotation_keeps_aggregate_abi
```

The real source-frozen Stage1 boundary reports:

```text
marshal_to_object: DynType with IR { i64, i64, i64, i64 } not supported in
IndexedFunctionKernel_instruction_fact
```

## Test [CONFIRMED]

The three-module regression fails deterministically.  `records.Quad` and the
same object re-exported as `provider.Quad` both carry `valueclass=True`, but
`provider.Source.read.return_ty` remains
`('class', 'Quad', '', (), ())`.  The consumer consequently emits
`call ptr @user_provider_Source_read` plus dynamic attribute lookup instead of
the aggregate call.

## Proposals

- No.1 Preserve imported valueclass projection while expanding public annotation descriptors [pending]
- No.2 Return a boxed object or duplicate the record in the kernel module [DENIED]

## No.1 Preserve imported valueclass projection while expanding public annotation descriptors

### Code Change

Trace the exported annotation descriptor from the defining class through the
imported name in the provider module.  Reconstruct the same valueclass
`ClassType` for the provider definition and its downstream callers, without a
module-name special case.  The local expansion becomes owner-aware and runs a
second idempotent pass after re-export and mixin merges have converged.
Class reconstruction must likewise key and resolve the schema by
`info.owning_module`, not by whichever re-export table exposed the class.
The parallel shared-export merger is a separate fixed-point implementation;
it must invoke the same post-merge expansion before serializing the export wire.
The real kernel also needs explicit `CompilerIntArena` annotations on its three
new fields.  Unlike the minimized provider's typed `arena: Arena` field, a
field introduced only by `self.attr = ...` in `__init__` remains dynamic in the
current frontend; its `get4_unchecked()` call therefore has a Dyn semantic type
even after the method's declared return ABI is correctly reconstructed.

### CONFIRMED

Require the three-module aggregate definition/call regression, the existing
two-module regression, the real value-arena/kernel/stackmap closure and the
source-frozen Stage1 boundary.

All listed boundaries pass.  The absolute and relative three-module tests keep
the provider definition and consumer call on `{ i64, i64, i64, i64 }`; the
existing two-module test remains green.  The real cache-off parallel closure
contains direct aggregate definitions/calls for the arena and kernel, and the
source-frozen pcc1 builds with no libpython/LLVM and passes the strict i64x4
gate.

## No.2 Return a boxed object or duplicate the record in the kernel module

### Code Change

Hide the missing imported annotation projection by changing the compiler
record API or defining another four-int class beside each consumer.

### DENIED

This restores object projection or duplicates semantic records, contradicts
the value-model contract, and repeats the workaround denied by the predecessor
investigation.

## Report

Proposal No.1 landed.  Export descriptors are expanded after both sequential
and parallel re-export/mixin convergence; class reconstruction keys the true
owner's schema; exact compiler record fields/locals are explicitly typed.  No
boxing, duplicate record class or module-name special case was introduced.

Final focused evidence:

```text
tests/python/test_cross_module_valueclass_abi.py       3 passed
full native-data-plane focused packet                123 passed
real cache-off kernel parallel closure                passed
source-frozen pcc1 no-libpython/self build             passed
pcc1 unsafe-i64x4 aggregate regression                 passed
```
