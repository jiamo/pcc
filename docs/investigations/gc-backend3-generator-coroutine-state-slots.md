# Investigation: Backend #3 generator and coroutine state slots

## Status
resolved

## Problem Description
Backend #3 relies on old object owners participating in the remembered-set when
they store young children. `py_gen_new()` and `py_coroutine_new_native()` still
allocated their owner objects with raw `malloc()`, and their runtime state slots
(`send_value` and cached `result`) were updated with direct pointer writes.

That leaves generator/coroutine state outside the generational owner model and
can bypass the write barrier and Backend #4 read barrier.

## Repro
Focused gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy' \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy' \
  -q -n0
```

Expected result: both C runtime and pcc-Python runtime variants pass. The probe
promotes a generator/coroutine owner, stores a young object into `send_value` or
`result`, forces a minor refill, and checks the owner slot points at the
non-minor oldified copy.

## Test [N/A]
The gap was found by code inspection while closing Backend #3 state-root
coverage. The focused gate above is the regression guard.

Observed result:

```text
2 passed in 28.60s
```

## Proposals
- No.1 Make generator/coroutine state GC-aware     [CONFIRMED]

## No.1 Make generator/coroutine state GC-aware
### Code Change
Allocate generator and coroutine owner objects with `pcc_gc_alloc()` in both C
runtime and pcc-Python runtime mirror. Route `frame`, `send_value`, `captures`,
`args`, and `result` updates through `pcc_gc_store_ptr()`, and load those slots
through `pcc_gc_load_ptr()` before invoking runtime entry points or releasing
owned references.

### CONFIRMED
The focused C runtime + pcc-Python runtime gate passed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy' \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy' \
  -q -n0
```

Observed result: `2 passed in 28.60s`.

## Report (only when the investigation is closing)
Generator and coroutine owner objects now use the same GC allocation and
barrier discipline as other owned-slot runtime containers. The regression gate
proves Backend #3 can oldify and eagerly rewrite both `PyGenObject.send_value`
and the coroutine cached `result` slot in C runtime and pcc-Python runtime
archive builds.

This does not close the broader scheduler/coroutine task work. It only closes
the current synchronous generator/coroutine runtime-state slot coverage gap.
