# Investigation: freestanding GC object-slot contract ownership

## Status

resolved

## Problem Description

All five pcc-Python GC algorithms currently share one object-slot graph rule,
but that rule is still defined inside the managed `py_gc_backend.py` archive
object.  The object-layout and owned/borrowed slot classification are raw
runtime-kernel data and must have one strict freestanding production owner
before the managed GC object can leave the archive.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_object_slots.py
```

Expected pre-change result: the strict object-slot module and its public raw
ABI are absent.

## Test [N/A]

Use compiled C-ABI probes, rather than source-only assertions, to prove slot
addresses and owned/borrowed roles for the supported object-layout families.
Then prove exact LLVM/self/fresh-pcc1 closure, unique production archive
ownership and the existing five-GC semantic consumers.

## Proposals

- No.1 Move the one object-slot graph contract behind a strict ABI [testing]

## No.1 Move the one object-slot graph contract behind a strict ABI

### Code Change

Export `pcc_gc_visit_object_slots(obj, visitor, context) -> handled` from one
strict pcc-Python module.  It owns object layout and slot-role enumeration and
invokes the caller-provided `void (*)(slot, role, context)` callback through a
verified indirect-call intrinsic.  Trace, promotion, update, subtraction,
clear and relocation retain separate caller-side action adapters without
duplicating graph rules.

### Result

Implemented in `freestanding_gc_object_slots.py`.  The initially considered
fixed `mode/recurse` action ABI was rejected because it would still bind the
layout owner to one managed consumer and could not serve backend 0 without a
second action surface.  The callback ABI mirrors the C slot contract and is
shared by `py_gc_backend.py` and `py_obj_gc.py`.

LLVM, self and fresh pcc1 objects define the same nine slot-contract symbols
and import exactly `pcc_capi_is_cext_type_tag`,
`pcc_capi_visit_cext_object_slots_i64` and `py_set_dummy`.  Compiled probes
cover core containers, fixed owners, weakrefs, continuations, classes,
C-extension delegation, instances and pointer-free/unknown tags.  Selected
production tests cover generational list/class oldification and backend4
list/task/instance relocation.  The production archive gives every contract
symbol one owner in `freestanding_gc_object_slots.o`.
