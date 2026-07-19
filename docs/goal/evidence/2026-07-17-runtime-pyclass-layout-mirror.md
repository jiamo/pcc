# PyClassObject runtime mirror closure — 2026-07-17

Task: `AUD-P1-RUNTIME-CLASS-LAYOUT-MIRROR`.

## Claim

The C `PyClassObject` remains the ABI owner, and its complete LP64 layout is
now mechanically checked against the pcc-Python mirror.  `del_method` has one
shared policy in both runtimes: it is a borrowed GC update-only alias, never a
second semantic finalizer cache.

## Changes and causality

- `py_internal.h` now has compile-time size/offset assertions for every
  `PyClassObject` field and both `PyClassMethod` fields, rather than only the
  final three class fields.
- A real C `sizeof`/`offsetof` probe is compared with the complete layout table
  in `py_class.py`; the same derived offsets guard both pcc-Python class slot
  visitors and the substrate object-root allocation.
- That guard exposed a real stale mirror: `py_substrate.py::py_subs_object_root`
  allocated and cleared 96 bytes while the C class object is 120 bytes.  It now
  allocates/clears 120 bytes, so `del_method`, `attrs`, and `metaclass` are
  inside the object rather than potential out-of-bounds tail accesses.
- C finalizer dispatch formerly read and lazily refilled `cls->del_method`,
  while pcc-Python always used `py_class_lookup`.  C now uses the same semantic
  MRO lookup.  Both class constructors still record the alias with the borrowed
  metadata barrier, and both GC walkers keep it update-only.
- A C behavior probe poisons the alias with a different function and proves
  finalizer dispatch calls the method-table owner without changing the alias.

## Gates

- Layout/source/update-only focused set:
  `3 passed, 27 deselected in 7.36s`.
- Post-fix real-layout probe:
  `1 passed in 0.34s`.
- Final required gate:
  `tests/python/test_gc_update_referents.py` plus
  `tests/python/test_native_class_attr_subclass_override.py`:
  `31 passed in 38.95s`.

No bootstrap, five-GC matrix, or GCC suite was run for this finite mirror
contract.

