# Investigation: Freestanding GC forwarding and identity substrate

## Status

resolved

## Problem Description

`LIBC-P2-FREESTANDING-GC` has strict pcc-Python owners for the shared GC
indexes, roots, tracing collection, and incremental/concurrent scheduling, but
Backend 3 copy-oldification still calls forwarding installation and stable
identity helpers implemented inside the large managed `py_gc_backend.py`
module. Moving generational promotion first would therefore preserve a reverse
dependency on the old monolith and leave two ownership boundaries for one
state machine.

This slice moves only the shared forwarding lookup/install, stable identity,
and relocation-read barrier substrate into one strict freestanding
pcc-Python module. Backend 4 zpage selection, relocation copying, remap, and
retirement remain out of scope and keep their existing policy owner.

## Repro

```bash
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_forwarding_identity.py
```

Before the slice, the strict source owner does not exist and the ownership
test fails while reading it.

## Test [CONFIRMED]

The initial owner test failed in 0.10 seconds because the strict source did not
exist.  The first implementation was then rejected because freestanding
helpers were not exported.  Exporting them first exposed a function/global
name collision for the two list heads; distinct `*_list_head` function names
closed that failure without renaming runtime state.

## Proposals

- No.1 Move the shared forwarding/identity substrate to strict pcc-Python [CONFIRMED]

## No.1 Move the shared forwarding/identity substrate to strict pcc-Python

### Claim boundary

- One strict source owns forwarding lookup/install, stable object identity,
  public relocation reads, and the small forwarding/identity counters.
- `py_gc_backend.py` consumes those operations through the declared runtime
  ABI and no longer defines the moved state-machine helpers.
- Both LLVM and self emitters produce raw objects with an exact, fail-closed
  import closure and no `py_cpy_*` fallback.
- The production pcc-Python archive has exactly one owner for every moved
  public symbol.

### Explicit non-claims

- No Backend 4 relocation-set selection, zpage policy, copying, remap, or page
  retirement moves in this slice.
- No claim of complete Backend 3 promotion or complete production-C GC
  removal is made by this slice.

### Gate

```bash
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_forwarding_identity.py
```

### CONFIRMED

- strict owner gate: 5 passed in 60.03 seconds;
- selected GC3/GC4 runtime semantics: 14 passed;
- fresh no-libpython self-backed pcc1: 39.062 seconds;
- the fresh pcc1 compiled the real module, clang accepted it, all 28 raw
  exports are definitions, and the IR has no `py_cpy_*` call/invoke target.

## Report

The shared forwarding/install/stable-identity/read-barrier substrate now has
one strict freestanding pcc-Python production owner.  This removes the key
managed reverse dependency required by Backend 3 copy-oldification while
leaving Backend 4 relocation policy explicitly open.
