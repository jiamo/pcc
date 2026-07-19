# Investigation: backend-4 relocation duplicates the shared object-slot contract

## Status

resolved

## Problem Description

Backend #4 deep-copies relocation payload storage in
`pcc_gc_relocate_copy_payload` and its pcc-Python mirror, but those functions
also enumerate every owned and borrowed `PyObject *` slot themselves.  The
runtime already defines the authoritative object graph through
`py_obj_visit_slots` / `_py_obj_visit_covered_slots` and `py_obj_update_slot`.
The second per-type slot rule can drift from tracing, promotion, clearing, and
remap semantics.

The finite boundary is only slot ownership and forwarding/remembered-set
retargeting during relocation.  Per-type copying of non-object payload storage
(dict indices, traceback records, class names, continuation metadata, and
similar raw buffers) remains necessary and is not a second graph rule.

## Repro

Run the structural source guard:

```bash
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_update_referents.py::test_backend4_relocation_reuses_shared_slot_contract
```

Before the change it fails because both payload-copy implementations contain
their own `py_incref` and remembered-slot retarget loops and do not invoke the
shared slot visitor for relocation copying.

## Test [CONFIRMED]

The structural gate is confirmed red on the pre-change implementation.  The
focused relocation behavior suite and five-backend production/bootstrap gates
will provide behavior proof after the source-of-truth migration.

## Proposals

- No.1 Pair source and target slots through the shared visitor [CONFIRMED]

## No.1 Pair source and target slots through the shared visitor

### Code Change

Keep per-type raw-payload allocation/copying in the relocation helper, but move
all object-slot healing, ownership retention, self-reference remapping, and
remembered-set retargeting into one generic source/target slot-pair pass driven
by `py_obj_visit_slots` in C and `_py_obj_visit_covered_slots` in pcc-Python.
Require matching slot counts and roles before accepting a relocated copy.

### CONFIRMED

Implemented one paired-slot pass in both runtime tiers. Per-type relocation
code retains only raw payload allocation/copying; the shared visitor determines
slot addresses and roles. C uses `py_obj_update_slot` before copying each slot,
and both tiers centrally retain only owned roles, rewrite self-references, and
retarget remembered slots.

The source guard, C and pcc-Python relocation probes, 125-node backend-4
production file, 140-node five-GC production contract, and the five-backend
self-backed fixed-point matrix all pass. Exact commands and summaries are in
`docs/goal/evidence/2026-07-11-gc4-relocation-shared-slot-contract.md`.

## Report

Proposal No.1 landed. Backend-4 relocation no longer owns a second per-type
object graph. Raw payload layout remains object-specific, while pointer-slot
ownership and updating are defined once by the shared visitor contract. No GC
index-table, forwarding-retirement lifetime, or broad write-barrier work was
mixed into this slice.
