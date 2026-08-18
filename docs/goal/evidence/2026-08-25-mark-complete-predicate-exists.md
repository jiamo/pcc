# The mark-complete predicate does exist — design for the sweep gate

`GC-P0-EXPLICIT-COLLECT-SWEEPS-WITHOUT-MARK-COMPLETE` was filed saying the
gating predicate "does not exist and cannot be synthesised from
has_tracing_sweep".  **That was wrong**, and reading the candidate-publication
site is what shows it.

## Where sweep candidates come from

`pcc_gc_finish_tracing_cycle` (C `py_gc_backend.c`, strict
`freestanding_gc_common_mark_cycle.py`) is the only place the flag is set —
one `flags_or(PY_FLAG_GC_SWEEP_CANDIDATE)` in the whole C tree, one
`flags | 1024` in the strict mirror:

```text
if (... || mark_active == 0) return;          only runs while the mark IS active
for each active object:
    WHITE ? set SWEEP_CANDIDATE : clear it    the atomic white->candidate cut
trace_cursor = NULL
gray_count = 0
mark_active = 0                               published together with the cut
```

So candidates are published **only at cycle finish**, atomically with clearing
`mark_active`.  Both mirrors are structurally identical here.

## Therefore

```text
mark_active == 0 && has_sweep_candidate() != 0
```

is exactly "a mark cycle completed and its sweep has not run yet".  It is a
sound gate for `pcc_gc_collect_tracing()`.

And the existing gate is unsound for a reason that now has a name:
`pcc_gc_has_tracing_sweep()` tests the candidate flag **without** consulting
`mark_active`, so candidates left over from a previous cycle whose sweep did not
finish make it true *while a new mark is in flight*.  That is precisely the
DENIED regression from earlier today — the state-based drain reached that
combination more often and swept mid-mark, freeing an object the in-flight
`py_list_contains` still needed.

## What this changes about the two earlier attempts

- The threshold drain (`idle_rounds >= 2`) was reverted for calling
  `collect_tracing` under an unsound gate.  Adding `mark_active == 0` to the
  gate removes that objection at the source, independently of how the loop
  terminates.
- The state-based drain was DENIED for the same unsound gate, not for draining
  on state.  Draining on state is fine *once the sweep is gated properly*.

So the correct change is smaller than either attempt: fix the **gate**, not the
loop.  A liveness bound is still wanted for the phase-boundary case (step
returns zero while the mark is active and not finished), but that is a
termination concern, not a correctness one.

## Correction to the task row

The row claimed no such predicate could exist.  It can, and it needs no new
state machine — only `mark_active` alongside the flag the gate already reads.

## Nonclaims

- Nothing implemented yet in this file; this is the design result.
- Not established: whether `pcc_gc_finish_tracing_cycle` is the only writer in
  every build configuration (checked on the current tree, both mirrors).
- The phase-boundary zero (measured 1, 0, 6 on backend 1) is still unexplained
  and still needs a liveness bound.
