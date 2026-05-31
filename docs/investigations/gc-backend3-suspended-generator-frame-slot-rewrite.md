# Investigation: Backend #3 suspended generator frame slot rewrite

## Status
resolved

## Problem Description
The GC production goal requires Backend #3 to preserve and update references held
outside the native C stack. A pcc generator stores its suspended Python frame in a
heap list (`py_gen_new(resume, frame)`), so an old generator frame that later
stores a young object must participate in the Backend #3 remembered-set and
oldification path.

This file narrows the broader scheduler/coroutine requirement to the generator
frame shape that exists today: `PyGenObject.frame -> PyListObject.items[i]`.

## Repro
Focused gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy' \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy' \
  -q -n0
```

Expected result: both C runtime and pcc-Python runtime variants pass. The probe
creates a generator, lets the generator own its heap frame list, promotes that
frame, writes a young child into the frame slot, forces a minor refill, and
checks that the frame slot points at the non-minor oldified copy rather than the
forwarded minor source.

## Test [N/A]
No known failing regression has been observed yet. This is a production-gap
coverage gate for the existing generator suspended-frame representation.

Observed result:

```text
2 passed in 28.37s
tests/test_gc_backend_generational.py: 23 passed in 234.37s
```

## Proposals
- No.1 Add focused suspended generator frame slot rewrite gate     [CONFIRMED]

## No.1 Add focused suspended generator frame slot rewrite gate
### Code Change
Add C-probe-backed pytest cases for `libpy_runtime.a` and
`libpy_runtime_pcc_py.a` in `tests/test_gc_backend_generational.py`.

### CONFIRMED
The focused gate passed for both runtime implementations:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy' \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy' \
  -q -n0
```

Observed result: `2 passed in 28.37s`. The full generational backend file also
passed: `23 passed in 234.37s`.

## Report (only when the investigation is closing)
The generator frame shape that exists today is covered for Backend #3 in both
runtime implementations. The test proves that `PyGenObject.frame` can own a
heap list frame, the frame can survive as an old object, and a later young child
stored in the suspended frame slot is oldified and the slot is eagerly rewritten.

This closes only the generator-frame slice. It does not close the broader
scheduler/coroutine work: stackless coroutine/task heap frames, scheduler queues,
and cross-domain remembered-set sharing still need dedicated implementations and
tests.
