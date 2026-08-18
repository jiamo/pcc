# Backend-4 FRESH_ALLOC widened to every relocatable tag; constructors publish

Date: 2026-08-27
Tasks: `GC-P1-BACKEND4-RELOCATABLE-TAGS-LACK-FRESH-ALLOC` (implemented),
`GC-P1-BACKEND4-SUSPENDED-EXECUTION-C-ARM-PUBLICATION` (dissolved into it).
Claim level: focused probe gates green on both runtime arms; five-backend
smoke green; NO bootstrap/five-GC-matrix claim (deferred with the rest of
today's work on kernel-lane source stability and machine memory recovery).

## Fix

`pcc_gc_alloc` (py_obj.c + py_obj.py, mirrored exactly) now grants
PY_FLAG_GC_FRESH_ALLOC to every tag the colored-relocation accept predicate
admits and that has a constructor: FUNC, ITER, GEN, COROUTINE, CONTINUATION,
TASK, EXC, CLASS, STATICMETHOD, MEMORYVIEW — on top of the original seven.
Each tag's constructor publishes on its success path
(pcc_gc_publish_initialized, a no-op off backend 4): py_gen_new,
py_iter x2, py_func_new, py_coroutine_new, py_continuation_new, py_task_new,
py_exc_alloc, py_memoryview_new, py_class_new — C and port both.
STATICMETHOD has no live constructor (the frontend erases staticmethod at
compile time); its list entry is defensive.

## Row B dissolved

The "strict passes / C fails rc=24, unusual mirror drift" framing was a
probe artifact: the failing assertions live in a `#if PCC_PROBE_RAW` block
compiled ONLY for the C arm, so the strict arm never ran them.  rc=24 was
`pcc_gc_alloc(56, PY_TYPE_GEN, 0)` lacking FRESH_ALLOC — exactly Row A's
tag gap.  No mirror drift existed; one fix closes both rows.

## The regression this slice introduced and caught (recorded in full)

The first port edit crashed EVERY GC4 binary at startup (SIGSEGV, stack
overflow, minimal repro `def main: print(7)`), while GC0-3 and the C arm
stayed green and all 8 probe tests PASSED — the probes are raw C mains and
never execute compiled-module init.  LLDB mid-recursion backtrace gave the
cycle:

```text
pcc_gc_alloc+292 -> py_module_attr_get -> module_name_key -> py_str_new
    -> pcc_gc_alloc -> (same)
```

`PY_TYPE_CLASS` was an UNBOUND NAME in py_obj.py: the port compiles an
unresolved global as a runtime module-attr lookup (the documented
port-unresolved-name hazard), the lookup allocates a str key, and an
allocation inside pcc_gc_alloc recurses forever.  The import-verification
step had reported PY_TYPE_CLASS as present because `PY_TYPE_CLASSMETHOD`
contains it as a substring — membership checks on identifier lists must be
boundary-exact.  Fixed by importing PY_TYPE_CLASS; bisect receipts: removing
all ten -> green; +halfA/+TASK,EXC -> green; +CLASS -> crash; import fix ->
five backends green.

## Gates

```text
tiny compiled program            PCC_GC_BACKEND=0..4 all rc=0
constructor-publication probes   8/8 (c + pcc_python arms)
full gcsubstrate_f file          30 passed; 4 tests deselected - all four
                                 are PRE-EXISTING HEAD reds (controlled in a
                                 pure-HEAD snapshot, same pcc_python-arm
                                 failures), the known port list-relocation
                                 reentry family
GC4 frontend gates               error-paths + owned-method + variants
                                 23 passed under PCC_GC_BACKEND=4
```

## Open boundary

- Exploitability demonstration (an actual mid-construction relocation copy
  observed pre-fix) was not built; the probes prove admission-blocking
  mechanics (unpublished objects cannot enter the relocation set).
- The four port list-relocation-reentry reds are pre-existing and separate.
- CLASS relocation itself deserves scrutiny: instances hold UNCOUNTED raw
  class pointers (PY_FLAG_IMMORTAL comment in py_class_new), so relocating
  a CLASS would leave every instance's raw pointer stale unless every read
  goes through the barrier — worth its own row before backend 4 ever
  actually evacuates a class.
- Bootstrap/five-GC matrix evidence rides the machine-recovery +
  kernel-lane-stability checkpoint with the rest of today's slices.

## Addendum (same day): the four port list-relocation-reentry reds are FIXED by this slice

After the final slice state (FRESH list + publishes + the PY_TYPE_CLASS
import fix), the four pre-existing pcc_python segfaults
(list remove_equality / clear_native_finalizer / delete_slice_finalizer /
set_slice_finalizer) pass on BOTH arms — 8/8, and the whole
gcsubstrate_f_backend4_growth_publication.py file is 38/38 with nothing
deselected.  The earlier "30 passed" run had deselected them and the final
state was never retested against the four; a pure-HEAD snapshot control
remains red.  Mechanism attribution (which exact edit) is the row's open
boundary — candidates: the EXC-constructor publish (the remove-miss path
raises inside the reentrant window), FRESH on the widened tags interacting
with mid-callback pcc_gc_relocate_copy, or the unbound-name fix.
