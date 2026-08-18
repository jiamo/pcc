# Raw-allocation probes must publish — 2026-08-25

## Claim

Two Backend-4 gates were failing for the same reason: their C probes built
objects with raw `pcc_gc_alloc` and never called `pcc_gc_publish_initialized`,
so the objects permanently carried `PY_FLAG_GC_FRESH_ALLOC` and could never
enter the relocation set.  Both are fixed in the probes.  **No runtime file was
changed.**

- `tests/python/test_freestanding_gc_relocation_drain.py` -> `8 passed`
- `tests/python/test_gc_backend_relocating.py` -> `24 passed in 163.03s`

## The rule this establishes

`pcc_gc_alloc` sets `PY_FLAG_GC_FRESH_ALLOC` for container type tags
(`py_obj.c:331`).  `pcc_gc_relocation_set_add_preallocated` refuses any object
carrying it — relocating a half-initialized object is not safe.  Real
constructors clear the flag by calling `pcc_gc_publish_initialized`
(`py_obj.c:380`) once initialization completes; `py_set_new`, `py_dict_new` and
friends all do.

**A test probe that hand-rolls an object with `pcc_gc_alloc` and skips the
publish has silently opted that object out of relocation.**  The failure mode is
not an error: selection simply reports zero, with no indication why.  That cost
a long diagnosis on the drain gate before the same pattern was recognised
immediately in the second.

## Why the second one was not assumed

The task/queue probe's `new_reloc_payload()` has the same raw-alloc shape, but
two of its three checks (`check_scheduler_queue_pop_slot`,
`check_scheduler_queue_free_slot`) were already passing while only
`check_task_result_slot` failed — so "same cause" was a hypothesis, not a
deduction.  It was tested by applying the publish and running the gate, which
went green, and the full file confirms no sibling regressed.

## Correction to an earlier claim

`2026-08-25-backend4-selector-page-only-diagnosis.md` states that the
three-symptom grouping on `GC-P1-BACKEND4-AGING-MIDSTOP-PROMOTION` was wrong.
That is too strong.  Accurately: **two of the three symptoms did share one
cause** (drain oracle and task/queue probe, both raw-alloc-without-publish), and
the third (aging mid-stop `promotions=0`) does not — its objects come from
`py_list_new(0)`, which publishes.  The original grouping was right about two
and wrong about one.

## Remaining

`test_colored_generation_aging_polls_only_after_releasing_graph_lock[c]` is
still red and deliberately unmodified — see
`2026-08-25-backend4-aging-midstop-expectation.md` for why relaxing its
`16/16` to `0/0` would leave a gate that passes without testing its property.

## Nonclaims

- No GC defect was found or fixed in any of the three symptoms.  Two were probe
  bugs; the third is a stale test expectation over correct runtime behaviour.
- The filter disagreement that made this hard to diagnose
  (`GC-P1-BACKEND4-FRESH-ALLOC-FILTER-DISAGREEMENT`) is untouched.
- No bootstrap, stage, fixed-point or five-GC gate was run.
