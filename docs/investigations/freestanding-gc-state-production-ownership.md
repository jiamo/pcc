# Investigation: move production GC state storage into a freestanding object

## Status

resolved

## Problem Description

`LIBC-P2-FREESTANDING-GC` requires the five collector kernels to form a strict
freestanding pcc-Python closure.  The production algorithm and telemetry
members (`py_gc_backend.o`, `py_obj_gc.o`, and `py_gc_telemetry.o`) are already
compiled from pcc-Python, but their raw global state is declared by the broad
managed `py_substrate.o` member.  This makes collector state ownership depend
on a high-level semantic module and prevents a precise link-map proof of the
raw GC kernel boundary.

## Repro

```bash
nm -A ~/.cache/pcc/test-artifacts/runtime-builds/*-pcc-py/\
libpy_runtime_pcc_py.a | \
  rg ':py_substrate\.o:.* [DBSC] _(py_gc_|pcc_gc_)'
```

Observed 2026-08-03: 130 GC state definitions, including
`py_gc_enabled`, `pcc_gc_backend_selected`, `pcc_gc_root_slots`,
`pcc_gc_object_head`, and backend-4 page/relocation state, are attributed to
`py_substrate.o`.

## Test [CONFIRMED]

The production archive ownership was inspected with `nm -A`.  The three
pcc-Python GC members list these names as undefined, while `py_substrate.o`
defines them.  A focused test will gate exact initial values, LLVM/self object
parity, zero undefined symbols, Makefile membership, unique production archive
ownership, and absence from `py_substrate.o`.

## Proposals

- No.1 Extract declarations into strict `freestanding_gc_state.py` [CONFIRMED]
- No.2 Mark all of `py_substrate.py` freestanding [DENIED]
- No.3 Leave state in the managed substrate until the full algorithm moves [DENIED]

## No.1 Extract declarations into strict `freestanding_gc_state.py`

### Code Change

Move only GC-owned i32 and pointer global definitions, with their exact initial
values, into a definition-only strict freestanding module.  Keep class caches,
exception TLS, singletons, and other semantic substrate storage in
`py_substrate.py`.  Add the new object to `FREESTANDING_PY_MODULES` and prove
its archive ownership independently of the later algorithm split.

### CONFIRMED

`freestanding_gc_state.py` now owns all 130 `py_gc_*` / `pcc_gc_*` raw
state definitions. An AST comparison against the former `py_substrate.py`
definitions proved identical names, storage kinds, and initial values; the
managed substrate retains none of them. Both LLVM and self objects have zero
undefined symbols and export exactly those 130 data symbols.

The production archive contains both `py_substrate.o` and
`freestanding_gc_state.o`, but `nm -A -g` attributes all 130 GC state symbols
to the freestanding object and zero to the substrate. A direct archive link
probe checks representative scalar/pointer initial values and mutation. A
strict self/no-libpython Python program linked against that same archive reads
and changes the public GC state and runs successfully under GC0..4.

## No.2 Mark all of `py_substrate.py` freestanding

### Code Change

Apply `__pcc_freestanding__ = True` to the existing substrate module without
splitting it.

### DENIED

The module owns high-level exception tables, singleton/class construction,
object-root cache semantics, and exported helpers with managed-runtime calls.
Relabeling it would either fail the verifier or weaken the freestanding
contract.

## No.3 Leave state in the managed substrate until the full algorithm moves

### Code Change

Defer storage ownership and attempt to split the 8,351-line backend first.

### DENIED

The state declarations are a closed, behavior-preserving, zero-dependency
slice with direct link-map evidence.  Keeping them coupled makes the much
larger algorithm split harder to audit and provides no semantic benefit.

## Report

### Root cause

The production GC algorithms were already emitted from pcc-Python, but their
raw storage was declared in a broad managed semantic module. That historical
placement made the collector kernel's dependency boundary impossible to prove
from the production link map even though the declarations themselves require
no managed runtime.

### Resolution

The declarations moved unchanged into a definition-only strict freestanding
pcc-Python module and the Makefile now archives its object through
`FREESTANDING_PY_MODULES`. Existing generational/backend-4 parity tests now
look for state ownership in that module. Class caches, exception TLS,
singletons, weakref state, and other semantic substrate data deliberately stay
outside this slice.

### Evidence

```text
3 passed, 1 deselected in 1.42s
  LLVM/self raw ABI + initial-value harness and archive plan

11 passed, 237 deselected in 50.99s
  affected frame-root, generational, backend-4 and substrate parity tests

4 passed in 44.27s
  first content-addressed production archive build and ownership/link probes

1 passed in 0.65s
  current production archive runtime under PCC_GC_BACKEND=0..4

5 passed in 2.33s
  final focused freestanding GC-state suite

production archive nm ownership:
  freestanding_gc_state.o: 130 GC state definitions
  py_substrate.o:            0 GC state definitions

existing no-libpython pcc1 (2026-08-03 13:24:36) compiled the new module:
  zero undefined symbols; 130 GC state definitions
```

### Claim boundary

This resolves raw GC state ownership only. It does not claim that the full
collector algorithm closure is freestanding or that weakrefs/finalizers,
suspended roots, concurrent synchronization, relocation, fixed point, and
long-run performance gates are complete.
