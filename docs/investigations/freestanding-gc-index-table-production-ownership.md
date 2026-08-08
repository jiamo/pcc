# Investigation: move production GC pointer indexes from C to freestanding pcc-Python

## Status
resolved

## Problem Description

`LIBC-P2-FREESTANDING-GC` requires every production GC symbol family to come
from pcc-Python objects.  Although the five collector algorithms already came
from `py_gc_backend.o`, the production `libpy_runtime_pcc_py.a` still contained
the CC-built `py_gc_index_table.o`.  That object owned the primary object-node
index plus forwarding, forwarding-target, stable-identity, frame, zpage-owner,
zpage-page, and object indexes used across GC0..4.

The replacement could not use a Python `dict` or any other managed object: the
index is required while implementing allocation, tracking, relocation, and
root discovery for that managed runtime.

## Repro

Before the slice, the production archive named the C object directly:

```text
ar -t pcc/py_runtime/libpy_runtime_pcc_py.a | rg gc_index
py_gc_index_table.o
```

The Makefile also listed `$(OBJDIR_PY)/py_gc_index_table.o` in
`OBJ_PY_CC_HELPERS` and compiled it with `$(CC)`.

## Test [CONFIRMED]

`tests/python/test_freestanding_gc_index_table.py` compiles the replacement
with both LLVM and the self backend, runs the same C-ABI harness against the
retained C oracle and both generated objects, and covers collision chains,
tombstones, resize/rehash, duplicate insert, upsert, replace, clear, null
nodes, tagged-int rejection, and raw odd-address frame/page keys.

It also builds the content-addressed production archive, proves the old member
is absent, proves the new member uniquely defines the public ABI, and links the
behavior harness directly against that archive.

## Proposals

- No.1 Port the open-addressed tables to strict freestanding pcc-Python [CONFIRMED]
- No.2 Reuse the managed Python dictionary implementation [DENIED]
- No.3 Retain the C table as a permanent GC-kernel exception [DENIED]

## No.1 Port the open-addressed tables to strict freestanding pcc-Python

### Code Change

Add `py/freestanding_gc_index_table.py`, using only raw pointer loads/stores,
integer operations, compiler-defined globals, and the owned `calloc/free` ABI.
Preserve every existing public C symbol and the oracle's exact return and key
filtering contracts.  Build `freestanding_gc_index_table.o` in
`FREESTANDING_PY_MODULES`, mark the differently named C source as replaced,
and remove `py_gc_index_table.o` from `OBJ_PY_CC_HELPERS`.

The freestanding verifier now distinguishes exact calls to definitions inside
the same verified module from external `pcc_gc_*` references.  This permits a
freestanding module to implement a GC ABI while still rejecting any reference
that escapes to the managed runtime.

### CONFIRMED

The focused suite reports `28 passed in 5.49s`.  A separate production
five-backend allocation smoke reports `1 passed in 0.47s`.  LLVM and self
objects have only `calloc` and `free` undefined, both satisfied by the owned
freestanding allocator in production.

A fresh current-source self/no-libpython stage1 completed in 57.382 seconds.
That pcc1 compiled the module in 0.68 seconds, preserved all pointer/integer/
void C-ABI signatures, emitted no managed-runtime body calls, and produced an
object whose only undefined symbols are `calloc` and `free`.

## No.2 Reuse the managed Python dictionary implementation

### Code Change

Represent each pointer index with an ordinary Python dictionary.

### DENIED

The dictionary allocates and traces managed Python objects.  Using it beneath
GC allocation, root lookup, or relocation would create the exact circular
dependency prohibited by the task's freestanding closure.

## No.3 Retain the C table as a permanent GC-kernel exception

### Code Change

Leave `py_gc_index_table.c` in the production archive and classify it as a
low-level kernel primitive.

### DENIED

The table is an algorithmic data structure expressible through the owned raw
memory and allocator primitives.  Retaining it would directly contradict the
task's link-map exit criterion.  The C source remains only as a differential
oracle for the C-runtime comparison tier.

## Report

No.1 landed in the working tree.  The production archive now contains
`freestanding_gc_index_table.o`, contains no `py_gc_index_table.o`, and
attributes every index ABI definition to the new object.  The larger
`LIBC-P2-FREESTANDING-GC` task remains incomplete: the algorithm modules still
need a strict freestanding-closure audit, `pcc_gc_external_resource.o` remains
a production C GC-adjacent object, and the final five-GC semantic/fixed-point
and long-running performance gates have not yet run.
