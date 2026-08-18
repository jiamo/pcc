# Investigation: strict decref ignores dynamic C-extension type tags

## Status

resolved

## Problem Description

The strict pcc-Python `py_obj.py::_py_decref_prepare` rejects every object with
`type_tag > 500` before decrementing it.  Runtime-created C-extension objects
use dynamic tags starting above the builtin range, so the strict runtime clears
their owner slot without releasing the owned reference.  The C owner already
exempts tags accepted by `pcc_capi_is_cext_type_tag` from the invalid-tag guard.

This was exposed while proving that GC4 list clear publishes its empty state
before a last-reference deallocator re-enters and relocates the same list.  It
is a distinct C/strict refcount parity bug, linked from
`gc-backend4-relocation-mutator-quiescence.md` rather than folded into the list
transaction claim.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  'tests/python/test_gc_threading_substrate.py::test_backend4_list_clear_finalizer_relocates_and_reenters_published_empty_list[pcc_python]'
```

Expected: exit 0; the dynamic C-extension deallocator runs once after list
length zero is published.

Observed on 2026-08-24: pytest exits 1; probe exits 7 and prints
`finalizer_hits=0 observed_length=-1`.  The same C-runtime parameter passes.

## Test [CONFIRMED]

The repro above deterministically observes the missing strict decref/dealloc.
The probe drops its construction reference after appending the object, so list
clear owns the terminal reference.  No timing or test-only publication hook is
used.

## Proposals

- No.1 Mirror only the C dynamic-tag exemption in strict decref preparation [DENIED]
- No.2 Register strict C-extension allocation, ownership and deallocation as one lifecycle [pending]

## No.1 Mirror the C dynamic-tag exemption in strict decref preparation

### Code Change

Import the existing `pcc_capi_is_cext_type_tag` runtime ABI in `py_obj.py` and
make `_py_decref_prepare` reject an out-of-builtin-range tag only when the
runtime C-API registry also says it is not a dynamic extension tag.  Add a
focused source contract that keeps the strict predicate aligned with the C
owner, then rerun the C/strict deallocator probe and strict closure.

This proposal does not widen arbitrary unknown tags: the registry remains the
single acceptance authority.

### DENIED

The exact guard mirror compiled and passed strict closure, but the original
probe still reported `finalizer_hits=0`.  Instrumenting the existing hot-cache
probe produced:

```text
tag=65536 registry=1 managed=0 known=0 refs=1,1,0,0
```

The dynamic tag is registry-proven, but the strict runtime does not register
the object as managed/known.  List append therefore did not acquire an owned
reference (`1 -> 1`), and the caller drop reached zero before clear.  Accepting
the tag in decref validation alone would bless an object whose allocation,
retain and deallocation lifecycle is still outside the strict ownership
contract.  The tag-only source change was removed with a forward patch; a
focused negative contract keeps unknown/unmanaged high tags fail-closed.

## No.2 Register strict C-extension allocation, ownership and deallocation as one lifecycle

### Code Change

Trace the `PyType_GenericNew`/C-API allocation path used with the strict runtime
archive and establish one C/strict contract for pointer registration, initial
refcount, list retain, caller release, terminal split-store release and dynamic
`tp_dealloc` dispatch.  Only after the object is managed/known may strict
decref mirror the registry-proven dynamic-tag exemption.

### pending

This broader C-API ownership task is not required to prove the list-clear
transaction itself, which has a native-instance finalizer supported by both
runtime implementations.  It remains queued independently.

## Update 2026-09-03 — resolved via the BOTH-guards exemption (No.1 was incomplete)

No.2 (register a full managed/known lifecycle) turned out not to be required.
The measured gap in No.1 was narrower than "not managed/known": No.1 only added
the exemption to `_py_decref_prepare`, so `_py_incref_prepare` still rejected
the `tag > 500` C-extension tag and the container retain failed (the observed
`1 -> 1`). Adding the identical registry-gated exemption to BOTH
`_py_incref_prepare` and `_py_decref_prepare` (mirroring the C owner's
`(invalid || >500) && is_cext == 0` guard) makes the strict refcount lifecycle
match C exactly with no GC-index registration -- consistent with the C runtime,
which does not GC-track C-extension objects and drives them by tag + refcount +
`pcc_capi_dealloc_cext_object`.

Proven by a C/strict differential (direct refcount + terminal list split-store)
plus a parity/negative source contract:
`tests/python/test_cext_strict_decref_lifecycle.py` (5 passed). Regression
`test_gc_update_referents.py` 35 passed; py_obj/py_list/py_capi_cext_runtime/
py_capi_type_runtime no-libpython closures OK; board validate OK. Evidence:
`docs/goal/evidence/GC-P0-CEXT-STRICT-DECREF-TAG-PARITY/001-both-guards-cext-parity.md`.
