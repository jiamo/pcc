# Dict delete split commit slice — 2026-08-25

## Claim

`py_dict_del` in C and strict pcc-Python now routes through the same rooted,
restartable probe as dict get and dict set.  On a stable match it commits
`entry->key -> NULL`, `entry->value -> NULL`, the index tombstone and the
decremented size together under one graph lock built from two store plans, and
finishes both plans only after unlock.  Absent keys still return `-1`.

Together with `2026-08-25-dict-set-callback-commit.md` and
`2026-08-25-set-add-update-callback-commit.md`, this closes the dict and set
mutation surface of `GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT`.  It is not
Stage1, Stage2, fixed-point, five-GC, or performance evidence.

## What was wrong

The legacy `py_dict_del` computed the owner and table before user `__hash__`
and `__eq__`, then released the key and the value **before** clearing the entry
slot, tombstoning the index and decrementing size:

```c
py_decref(py_dict_entry_key(d, e));
py_decref(py_dict_entry_value(d, e));
e->key = NULL;
e->value = NULL;
d->indices[slot] = PY_DICT_TOMBSTONE;
d->size--;
```

A finalizer reached from either release re-enters a dict whose index still
points at an entry whose key has already been freed.  That is a use-after-free
on every backend, not only the moving ones.  The strict mirror had the same
ordering.

## Focused evidence

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_threading_substrate.py::test_dict_del_commits_tombstone_and_size_before_releasing_key_and_value
```

Result: `1 passed in 0.49s`.

```text
gtimeout 600s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_threading_substrate.py -k "dict_del_relocates"
```

Result: `2 passed, 192 deselected in 3.04s` (C and strict pcc-Python).

```text
gtimeout 900s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_python_dict_methods_parity.py \
  tests/python/test_python_set_methods_parity.py
```

Result: `23 passed in 37.85s`.

C `py_dict.c` compiles with no errors and no unused-function warnings under
`-Wall -Wextra` in both thread modes.  `scripts/check_layer1_ownership.py`
passes.

## Proven by the dynamic probe

- a C-extension `tp_richcompare` relocates the dict during the delete probe;
  the delete still reports success, `len == 0`, and the key is absent;
- the detached value's pcc-native `__del__` re-enters the dict and observes
  `len == 0`, `py_dict_entry_key_at(d, 0) == NULL`, and no pending exception —
  the release ran against the committed table;
- deleting the same key again still returns `-1`;
- the scheduler-root count is balanced at 1.

## Probe correction

The first version of the probe asserted the finalizer inline and failed with
`calls=0`.  That was a real property of the runtime, not of the change: the
value was released while the dict still carried a forwarding shell, so its
release is completed on a retirement epoch.  The probe now drives two
`pcc_gc_backend4_remap_and_retire_stopped_world()` epochs first, matching the
proven set-remove precedent in Proposal No.21.

A `#if PCC_PROBE_STRICT` carve-out was drafted for the strict mirror on the
assumption that it would inherit the open strict forwarding-source gap recorded
under `GC-P0-FORWARDED-SOURCE-PAYLOAD-RETIREMENT`.  Measurement showed the
strict mirror **does** run the finalizer here, so the carve-out was removed and
both mirrors face the identical assertion.  Do not reintroduce it from the
set-remove precedent alone.

## Incidental repairs — three stale static-contract markers

Three tests in `test_gc_threading_substrate.py` sliced runtime source on
markers that HEAD had already moved, so they errored before asserting
anything.  All three were repaired without changing what is asserted:

1. `test_generational_backend_step_polls_thread_safepoint_in_c_and_pcc_python_runtime`

`test_generational_backend_step_polls_thread_safepoint_in_c_and_pcc_python_runtime`
   split on `def pcc_gc_step(budget: int) -> int:` while the dispatcher has
   carried `def pcc_gc_step(budget: i64) -> i64:` since before this slice.
   Only the split string changed; the `pcc_thread_safepoint()` assertion is
   unchanged and passes on substance.
2. `test_tracing_gc_finalizer_handles_thread_objects_and_refcount_side_table`
   asserted the magic literal `tag == 27`, but the sweep collector now
   dispatches on `abi_constant("object.type.thread")`.  The marker was updated
   to the named constant, which asserts strictly more than the literal did;
   `py_dealloc_thread_thread(obj)` was already intact.
3. Two markers this slice itself invalidated: the No.19 contract's expected
   `py_dict_get` call text after `out_status` was reintroduced for delete, and
   the No.23 replace-region slice, whose end marker was `py_dict_rooted_op`
   until `py_dict_del_rooted_slot` was inserted between them — which made the
   region swallow the delete function and its legitimate `&entry->key` write.
   The region now ends at the delete function.

None of these repairs weakened an assertion.  A fourth stale marker was
**not** repaired: see the routed reds below.

## Pre-existing reds found and routed, not absorbed

Four pre-existing failures surfaced during the neighbor sweep.  None is
attributed to this slice, and none was silently skipped.

Three are Backend-4 object-bookkeeping symptoms, plausibly one cause, filed
together as `GC-P1-BACKEND4-AGING-MIDSTOP-PROMOTION`:

- colored-generation aging reports `promotions=0 aged=0` against a required
  16/16;
- the relocation-drain **C oracle binary itself** exits 4, meaning
  `pcc_gc_select_relocation_set(8)` did not return 2 after two raw
  `PY_TYPE_LIST` allocations;
- the task/scheduler forwarding probe reports `[0,1,1]` instead of `[1,1,1]`,
  failing at `check_task_result_slot`.

The fourth is `GC-P1-CMS-CEXT-UNLOCK-CONTRACT-MARKER-DRIFT`: the strict CMS
contract slices `pcc_gc_tracing_step_cycle` up to the next `@c_abi_export` and
looks for `pcc_gc_trace_cext_referents_unlocked(`, but that call was factored
into `_pcc_gc_trace_cext_complete_context`.  Unlike the three marker repairs
above, deciding where that ordering contract now belongs requires reading the
new call structure, so the marker was **not** moved.

Attribution control, stated as what it is: this slice's diff is confined to
`py_dict.c` and `py_dict.py`; none of the three Backend-4 probes calls any dict
function; and the slice is green on 187 substrate nodes, 34 relocation
payload/retirement nodes and 23 dict+set parity nodes, including its own
Backend-4 relocation and finalizer probes.  That is an argument from coverage
and call graph, **not** a bisect — no reverted-baseline build was produced.

## Nonclaims

- `tests/python/test_gc_threading_substrate.py` completes at
  `187 passed, 4 skipped, 3 deselected in 126.64s` with the two routed reds
  deselected.  The 4 skips are pre-existing
  (`backend4_iterator_constructor_publication`,
  `backend4_suspended_execution_constructor_publication`) and are not this
  slice's to close.
- Relocation payload plus forwarding retirement plus relocation copy:
  `34 passed in 14.43s`.
- No broad bootstrap, stage, fixed-point or five-GC gate was run.
- The dead legacy probes `py_dict_lookup` / `py_dict_keys_equal` and their
  strict counterparts `_lookup` / `_keys_equal` / `_slot_of` / `_entry_idx_of`
  were removed from both mirrors, so no raw unrooted dict probe remains.
