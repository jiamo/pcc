# Investigation: pcc-Python GC3 oldify probes currently promote in place

## Status
resolved

## Problem Description

While validating the C-runtime fix for million-object GC3 release, two existing
pcc-Python runtime archive probes remained red after every experimental mirror
change was removed. The pcc-Python GC3 path promotes a young minor-arena object
in place (`OLD` remains together with `MINOR_ARENA`) instead of creating the
expected forwarded non-arena old copy. A direct string promotion probe likewise
finds no forwarding target after `pcc_gc_step()`.

This is independent of the C-runtime 1M release fix: `libpy_runtime_pcc_py.a`
replaces `py_gc_backend.c` with the compiled Python module, the Python source
was restored to its prior implementation, and the two failures still reproduce.

## Repro

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_oldifies_copy_for_remembered_child \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_release_of_forwarded_source_consumes_source_ref
```

Observed result on 2026-07-16: `2 failed in 68.88s`.

The first probe prints `0,1,1,1,1` instead of `1,1,1,1,0`: no distinct
forwarded pointer, the owner slot still names the source, and the source is both
old and minor-arena. The second exits at code 4 because
`pcc_gc_note_relocation_read(source) == source`.

## Test [CONFIRMED]

The two node ids above are the focused failing gate. Their C-runtime counterparts
remain green under the same working source, which provides the parity oracle.

## Proposals

- No.1 Diff pcc-Python generated oldify control flow against the C runtime [confirmed]

## No.1 Diff pcc-Python generated oldify control flow against the C runtime

### Code Change

`pcc/py_runtime/py/py_gc_backend.py` now makes
`_py_obj_visit_covered_slots()` return success for the explicit
`_py_obj_has_no_pointer_slots()` families, matching the C runtime's
`py_obj_visit_slots()` entry contract. The source-parity regression in
`tests/python/test_gc_update_referents.py` requires that shared entry check.

### confirmed

The retained failing archive contained the complete generated oldify control
flow. LLDB showed that tag 2 passed `_relocate_copy_supported_tag` with result
1, but `_relocate_slot_pairs_prepare` returned null and therefore
`_relocate_copy_payload` and `_generational_oldify_copy` returned zero. The
Python covered-slot entry omitted the C entry's explicit success case for
objects with no pointer slots, so an integer was misclassified as an
unsupported object layout.

After the one-entry mirror fix, the source contract passed (`1 passed in
0.29s`) and the two C oracle plus two pcc-Python oldify/forwarded-source probes
passed together (`4 passed in 80.12s`). Full evidence is in
`docs/goal/evidence/2026-07-16-gc3-pcc-py-oldify-parity.md`.
