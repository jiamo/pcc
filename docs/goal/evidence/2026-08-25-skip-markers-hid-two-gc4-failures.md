# Two `pytest.mark.skip` markers hid GC4 failures, and both reasons were wrong

Found while confirming a substrate run.  The file reported `203 passed, 4
skipped`; this repo's rule is run-or-deselect, never skip, so the four were
worth reading.  Two markers, each covering both mirror arms:

```text
@pytest.mark.skip(reason="strict GC4 FUNC/ITER allocation blocker is recorded ...")
@pytest.mark.skip(reason="strict GC4 suspended-execution fresh-admission blocker is recorded")
```

Both blame the strict (pcc-Python) runtime.  Lifting them and measuring gives:

```text
backend4_iterator_constructor_publication[c]               FAILED  rc=4
backend4_iterator_constructor_publication[pcc_python]      FAILED  rc=4
backend4_suspended_execution_constructor_publication[c]    FAILED  rc=24
backend4_suspended_execution_constructor_publication[pcc_python]  PASSED
```

Each reason is wrong, in opposite directions:

- the iterator failure is **not** strict-specific — it fails identically on the
  C arm, so the marker attributed a shared-path defect to one mirror;
- the suspended-execution failure is **backwards** — the strict arm passes and
  the C arm fails, so the marker both mislabelled the culprit and suppressed
  strict coverage that works.

## The iterator failure is a real gap, not a probe expectation bug

`rc=4` is the probe observing that a freshly `pcc_gc_alloc`'d `PY_TYPE_ITER`
carries no `PY_FLAG_GC_FRESH_ALLOC`.  My first read was that this was another
raw-alloc probe asserting something the runtime never promised — the class
already recorded in `2026-08-25-raw-alloc-probes-must-publish.md`.  Reading the
two paths says otherwise:

```text
py_obj.c:322-331          FRESH_ALLOC granted to LIST TUPLE DICT SET
                          PROPERTY CLASSMETHOD WEAKREF  -- and nothing else
py_gc_backend.c:14838     backends 1 and 2 set it for EVERY tag on their own
                          alloc path, so only backend 4 relies on that list
colored relocate tags     also accepts STATICMETHOD MEMORYVIEW FUNC ITER GEN
                          COROUTINE CONTINUATION EXC CLASS
```

`FRESH_ALLOC` is precisely what makes
`pcc_gc_relocation_set_add_preallocated` refuse a half-initialized object.  For
those nine tags the guard cannot fire, so under backend 4 a mid-construction
object is admittable to the relocation set.  The probe was pointing at
something real and the marker buried it.

## Why it is filed and not fixed here

Widening the tag list is not the fix on its own.  `FRESH_ALLOC` is cleared by
`pcc_gc_publish_initialized`, so if a constructor for one of those tags never
publishes, the flag stays set forever and **silently disables relocation for
that whole type** — a quiet behavioural regression that would look like a
performance change, not a bug.  Doing it properly means auditing every
constructor for nine tags, which is its own slice.  Filed as
`GC-P1-BACKEND4-RELOCATABLE-TAGS-LACK-FRESH-ALLOC` with that mechanism
recorded, plus the honest limit: the tag-list gap is read from source, while
exploitability is inferred rather than demonstrated.

The suspended-execution split is filed separately as
`GC-P1-BACKEND4-SUSPENDED-EXECUTION-C-ARM-PUBLICATION`.  Its cause is not
diagnosed; what is established is only that the strict mirror is the correct
one, which is unusual enough to be worth reading both sides before assuming.

## What changed in the file

The markers are gone and the tests are visible and red, with a comment at each
site recording the measured per-arm result and the task id.  This follows the
precedent already set by `test_colored_generation_aging_polls_only_after_
releasing_graph_lock`, which is known-red, filed, and excluded at the gate
command rather than skipped in-file.

Consequence for gate commands: the substrate file now needs three deselects
rather than one.  It was already not green before this change.

## Nonclaims

- No fix was attempted for either failure.
- Mid-construction relocation of an ITER or FUNC has not been demonstrated.
