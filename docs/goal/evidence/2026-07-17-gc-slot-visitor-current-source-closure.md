# Unified GC slot-visitor current-source closure

Date: 2026-07-17

Task: `AUD-P0-GC-SLOT-VISITOR`

## Outcome

The unified `py_obj_visit_slots` / `py_obj_update_slot` production contract is
closed over the finite current runtime surface.

The C contract routes every concrete current object tag into exactly one of:

1. explicit no-pointer objects;
2. core container slots;
3. fixed owner slots;
4. weakref owned/update-only slots;
5. continuation stack slots;
6. class owned/borrowed metadata slots;
7. C-extension `tp_traverse` slots; or
8. instance / ValueBox / dynamic user-class slots.

The type snapshot includes built-in tags 0 through 30, the CPython handle tag
32, ValueBox tag 200, and the `PY_TYPE_USER` dynamic threshold.  Property,
classmethod, and staticmethod descriptor tags are explicit fixed-owner
families; dynamic user-class tags enter the instance visitor; dynamic C-API
tags enter the extension visitor.  The three shim-owned C-API types added to
that extension surface (sequence iterator, ContextVar, and slice) now expose
their owned slots through `tp_traverse`.

`test_current_runtime_type_tags_have_a_finite_slot_classification_source`
freezes that classification.  Adding a new concrete `PY_TYPE_*` tag now fails
the structural suite until its pointer-slot class is declared.  Future object
types are therefore normal new work guarded by a red test, not an unbounded
open boundary on this completed task.

The C trace, cycle subtraction, promotion, relocation pairing/remap, and clear
consumers use the shared visitor.  The pcc-Python mirror uses the same ordered
seven-family helper, including the native C-extension slot bridge, for its
trace/subtract/promote/remap/clear consumers.  Backend-4 raw payload copying
remains type-specific, but object ownership and forwarding are determined by
the shared paired-slot visitor, as established in
`2026-07-11-gc4-relocation-shared-slot-contract.md`.

## Gates

Required direct payload roots under GC0 through GC4:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
5 passed in 1.50s
```

Current C/pcc-Python structural parity, dynamic C-extension probes, shared
consumer checks, root walkers, relocation pairing, and finite type snapshot:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_update_referents.py
27 passed in 27.58s
```

Required full production contract:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract
168 passed in 60.10s
```

Focused pcc1 user-program gate.  This reuses the existing stage-1 compiler and
links the current fresh pcc-Python runtime archive; it proves pcc1 can compile
and run the exception path no-libpython, not a current-source compiler fixed
point:

```text
gtimeout 180s env -u LC_ALL \
  PCC1_BINARY=build/bootstrap-pytest-shared-stage1/pcc1 \
  uv run pytest -q -n0 \
  tests/python/test_pcc1_python_smoke.py::test_pcc1_smoke_exception_handling
1 passed in 0.73s
```

No full GCC suite and no pcc1-to-pcc2-to-pcc3 or five-GC compiler-bootstrap
matrix was run.

## Claim boundary

This proves current C and pcc-Python object-graph consumers share the unified
slot contract for all concrete current runtime type families, with five-GC
behavior coverage for the production contract.  It does not pre-approve a
future object type, or prove that an arbitrary third-party extension supplies a
correct `tp_traverse`; the new type-snapshot guard and extension ABI contract
make those explicit follow-on obligations.
