# 2026-07-16 pcc-Python GC3 oldify parity evidence

Task: `G-P1-GC3-PCC-PY-OLDIFY-REGRESSION`

## Result

The pcc-Python runtime now classifies the explicit no-pointer-slot object
families as successfully covered at the shared `_py_obj_visit_covered_slots`
entry. This matches the C runtime's `py_obj_visit_slots` contract.

Before the fix, a GC3 young `PY_TYPE_INT` reached copy-oldification, passed the
supported-tag guard, and then failed `_relocate_slot_pairs_prepare`: the slot
visitor returned zero for an object that correctly has no pointer slots. The
caller consequently fell back to in-place `OLD | MINOR_ARENA` promotion, never
installed a forwarding entry, and did not rewrite the remembered owner slot.
LLDB over the retained failing probe confirmed the boundary: tag 2 support
returned 1, while `_relocate_slot_pairs_prepare` and the oldify helper returned
null on both attempts.

The fix is deliberately narrow: no forwarding, refcount, minor-arena, payload,
or C-runtime rule changed. A source regression now requires the pcc-Python
covered-slot entry to consume the same no-slot classification used by tracing,
promotion, remapping, and clearing.

## Focused gates

The source contract passed:

```text
gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_update_referents.py::test_no_pointer_slot_families_are_explicitly_classified_source

1 passed in 0.29s
```

The board-required C/pcc-Python parity gate passed in one final run:

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_oldifies_copy_for_remembered_child \
  tests/python/test_gc_backend_generational.py::test_generational_backend_release_of_forwarded_source_consumes_source_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_oldifies_copy_for_remembered_child \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_release_of_forwarded_source_consumes_source_ref

4 passed in 80.12s
```

The first pcc-Python runtime probe now produces a distinct non-arena old copy,
rewrites the owning slot, and resolves the source through forwarding. The
second establishes one forwarding entry, releases the forwarded source, then
observes zero entries and target refcount one (`1,0,1`).

## Claim boundary

This proves C/pcc-Python parity for the focused GC3 no-pointer-slot
copy-oldification and forwarded-source release contracts. It does not claim a
full pcc-Python runtime suite, bootstrap matrix, or five-GC bootstrap result;
none of those broad gates was run for this finite regression.
