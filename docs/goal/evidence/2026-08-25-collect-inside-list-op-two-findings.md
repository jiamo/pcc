# A collect taken inside a list operation: two distinct defects

Extending the collect-during-mutation probes to lists found two separate bugs.
Both reproduce in under two seconds and are isolated to a single variable.

## The probe and how it localizes

One probe, two phases, differing **only** in whether the operation holding the
callback mutates.  Same list, same extension-type `tp_richcompare`, same
`pcc_gc_collect(0)` call from inside it:

```text
contains   py_list_contains -- equality runs mid-scan, nothing is committed
remove     py_list_remove   -- equality runs mid-scan, then the list commits a
                               removal and releases the element
```

Each phase ends by checking a control: an unreachable two-dict cycle holding a
`__del__` instance, which refcounting alone cannot reclaim.  If it is never
finalized the probe fails, because a collector that collected nothing makes
every other assertion vacuous.

## Finding 1 — the C runtime stops collecting after `py_list_remove`

```text
remove phase             c            pcc_python
REFCOUNT_CYCLE (0)       pass         pass
INCREMENTAL_TRICOLOR (1) FAIL         pass
CONCURRENT_MARK_SWEEP(2) pass         pass
GENERATIONAL (3)         FAIL         FAIL (hang -- finding 2)
COLORED_RELOCATING (4)   FAIL         pass
```

On the failing arms the removal itself is **correct**: length 2, order intact,
and the removed element finalized exactly once.  What breaks is the collector.
Afterwards:

```text
collect returned 0 total, remove=1 contains=1 victim_final=1
```

Eight subsequent `pcc_gc_collect(0)` calls collect nothing, and the
known-unreachable control cycle is never reclaimed for the rest of the process.
That is an unbounded leak, not a corruption.

**It is the collector, not that one cycle.**  A brand-new unreachable cycle
built *after* the armed removal is also never reclaimed:

```text
collect returned 0 total, remove=1 contains=1 victim_final=1 fresh_final=0
```

This check exists because two earlier findings this session evaporated into
probe bugs, and "a retained cycle" and "a dead collector" look identical
without it.

**The trigger is the re-entrant collect, not the removal.**  In the `contains`
phase the removal still happens -- only its callback does not collect -- and the
control cycle is reclaimed normally.  So what kills the collector is calling
`pcc_gc_collect` from inside the equality callback *while `py_list_remove` is
scanning*, at a point where it holds three registered scheduler roots
(`list_handle`, `query_handle`, `candidate_handle`).

Three arms isolate it:

```text
no collect in any callback            control collected      pass
collect inside py_list_contains       control collected      pass
collect inside py_list_remove         collector dead         FAIL
```

So it is not the equality path and not the C-extension path — only the mutating
operation.  Adding allocation churn to the drain loop was tried and did not
help, which rules out "the incremental collector simply had no cycle
requested".

**The strict pcc-Python mirror is correct on backends 1 and 4 where the C
runtime is not.**  That is the same direction as
`GC-P1-BACKEND4-SUSPENDED-EXECUTION-C-ARM-PUBLICATION` found earlier today, and
it makes the strict implementation the reference for this fix rather than the
thing to bring into line.

Filed as `GC-P0-COLLECT-INSIDE-LIST-REMOVE-STOPS-COLLECTOR`.

## Finding 2 — the generational strict mirror hangs

```text
contains phase, pcc_python, GENERATIONAL_MINOR_MAJOR    timed out after 60s
remove   phase, pcc_python, GENERATIONAL_MINOR_MAJOR    timed out after 60s
```

Any collect driven from inside a list operation hangs the strict generational
runtime, mutating or not — so this is not the same defect as finding 1, which
needs the mutation and never hangs.  The C arm of `contains` passes on all five
backends, so it is strict-only.

Filed as `GC-P0-GENERATIONAL-STRICT-HANGS-ON-COLLECT-INSIDE-LIST-OP`.

## A read I got wrong on the way

Running the two phases together and looping over backends gave
`INCREMENTAL FAIL / GENERATIONAL FAIL / COLORED FAIL`, and I described that as
one bug affecting three backends.  It was two bugs with different symptoms —
one returns a wrong collector state, the other hangs — and splitting the phases
is what separated them.  A per-backend pass/fail column is not a diagnosis; I
should have read the failure text on each arm before summarizing the pattern.

## Gates

```text
-k "collect_during_list_op and contains"   c: 5 passed; strict: 4 passed, 1 hang
-k "collect_during_list_op and remove"     see the matrix above
```

The probe is left in the tree with its red arms visible, matching how
`test_colored_generation_aging_polls_only_after_releasing_graph_lock` is
handled: known-red, filed, excluded at the gate command rather than skipped
in-file.  Five of its twenty arms are red.

## Nonclaims

- No mechanism identified for either finding.  One structural difference was
  found and is *not* yet shown to be the cause: the C mutating block takes
  `pcc_gc_root_slot_lock()` where the strict mirror takes
  `pcc_py_gc_minor_graph_lock()`.  Since the collect happens in the callback
  *before* either lock is taken, this cannot be the mechanism on its own.  The
  pointer-write shape is identical in both (plain store plus `memmove`), so the
  load/store-barrier invariant is not the difference either.  `pcc_gc_mark_active_load` and
  `pcc_gc_graph_lock_depth` are both static, so neither is readable from a
  probe; the lock-reentrancy site at `py_gc_backend.c:1265` was checked and is
  not a bail.
- Whether other mutating list operations (`pop`, `insert`, `extend`) share
  finding 1 is not tested.
- No bootstrap, stage or fixed-point gate was run.

---

## Correction (same day) — finding 1 was overstated, and the matrix was wrong

Two errors of mine, both found by checking my own claims rather than by a new
symptom.

### The "dead collector" claim does not survive its control

I wrote that the collector "stops collecting for the rest of the process",
resting on two observations: eight later `pcc_gc_collect(0)` calls returned 0,
and a fresh unreachable cycle built after the removal was never reclaimed
(`fresh_final=0`).

Measuring the *healthy* arm at the same point kills both:

```text
contains (passes)   sweep_before=0 stepped=0 sweep_after=0 control_final=1 fresh_final=0
remove   (fails)    sweep_before=0 stepped=0 sweep_after=0 control_final=0 fresh_final=0
```

`pcc_gc_has_tracing_sweep` and `pcc_gc_step` report the **same idle state** on
both arms — the healthy collector is idle there too, because it has already
finished and reclaimed the control cycle.  And `fresh_final=0` holds on the
healthy arm as well, so the discriminator I introduced specifically to prove a
dead collector does not discriminate at all.

What remains established is narrower and does not name a mechanism: **the
control cycle is reclaimed when the collect is driven from `py_list_contains`
and not when it is driven from `py_list_remove`.**  Retitled accordingly and
dropped from P0 to P1.

### The per-backend matrix was partly inferred, not measured

`-k "... and c and ..."` is a substring match, and `c` also matches
`pcc_python`, so several runs I read as single-mirror had selected both arms
with `-x` stopping at the first failure.  Re-measured with explicit node ids:

```text
remove phase          c        pcc_python
REFCOUNT_CYCLE        pass     pass
INCREMENTAL_TRICOLOR  FAIL     pass
CONCURRENT_MARK_SWEEP FAIL     pass        <- I had reported this as passing
GENERATIONAL          FAIL     FAIL (hang)
COLORED_RELOCATING    FAIL     pass

contains phase        all pass except pcc_python + GENERATIONAL (hang)
```

This is a simpler and stronger statement than the one it replaces.  Backend 0
takes the early-return fast path in `py_list_remove` and never enters the
rooted path at all, so: **every backend that uses the rooted C
`py_list_remove` path fails, and the strict mirror fails on none of them.**

Finding 2 (the strict generational hang) is unaffected — a 61-second subprocess
timeout is a hang under any interpretation, and it reproduces on both phases.

### What this cost

Nothing was built on the wrong claim, because the correction came from checking
it rather than from a later failure.  But the two mistakes have the same root:
I ran a discriminator on the failing arm only, and I read a substring `-k`
selection as an exact one.  Both are one extra command.

---

## Finding 2 mechanised: a single `pcc_gc_step` never returns

The hang is a **livelock inside one `pcc_gc_step` call**, not a deadlock and not
`pcc_gc_collect`'s drain loop.

`sample` on the hung child, 2485 samples:

```text
py_list_contains
  user_py_list__list_equality_scan
    user_py_capi_cext_runtime__call_richcompare_slot
      pcc_gc_step                                     2201 of 2485
        pcc_gc_frame_enter
        pcc_gc_note_frame_enter
        pcc_gc_frame_node_alloc  ->  memset
        pcc_py_gc_minor_graph_lock
        pcc_gc_frame_index_replace_preallocated
```

`pcc_gc_collect`'s frame is absent because the tail-call pass elided it — the
callback calls `pcc_gc_collect`, whose body is
`for (;;) { stepped = pcc_gc_step(1024); if (!stepped) break; }`.  Reading the
stack as "the callback calls step directly" would have pointed at the wrong
function.

A bounded step census, replacing the collect in the callback with N direct
`pcc_gc_step(1024)` calls, separates "many slow steps" from "one step that
never returns":

```text
strict REFCOUNT_CYCLE          nonzero=0     step returns immediately
strict INCREMENTAL_TRICOLOR    nonzero=2     two steps of progress, then done
strict GENERATIONAL  N=3000    60s timeout
strict GENERATIONAL  N=50      60s timeout
strict GENERATIONAL  N=1       60s timeout   <- the FIRST step never returns
```

The `N=1` row is the one that matters: a single `pcc_gc_step(1024)` invoked
from inside a list equality callback does not terminate on the strict
generational runtime.  Combined with the stack, the work it loops on is GC
frame-node allocation — consistent with the strict port entering a GC frame per
call, so stepping re-enters frame machinery that generates more step work.

This is the shape a pcc-Python port produces and the C runtime does not, which
matches the arm split exactly: every C arm of the non-mutating phase passes.

Bounds on the claim: the loop is characterized, its termination condition is
not.  I did not identify which counter or worklist fails to drain.

## Nonclaims for finding 2

- The specific non-terminating loop inside the generational step was not
  located in source; only its call-stack neighbourhood.
- Not tested: whether a collect driven from inside a dict or set callback hangs
  the same runtime, or whether this is specific to the list scan.

---

## Finding 2, actually mechanised — and two retractions from the attempt above

The section above got the mechanism wrong twice.  Both errors are recorded here
because each has a cheap check that would have caught it.

### Retraction 1: the step does not loop on frame-node allocation

I read the `sample` tree as `pcc_gc_step` → `pcc_gc_frame_enter` →
`pcc_gc_frame_node_alloc`.  Those are **siblings**, not children:

```text
2485 user_py_capi_cext_runtime__call_richcompare_slot + 72
  2201 pcc_gc_step + 192            <- leaf, no children
  + 236 pcc_gc_frame_enter + 64     <- SIBLING, the callback's own frame setup
  + ! 29 pcc_gc_note_frame_enter
```

`sample`'s `+ ! : |` prefixes are tree drawing, and reading them as indentation
invents a call path.  The frame-node work is `call_richcompare_slot` setting up
its own frame and has nothing to do with the hang.

### Retraction 2: a single `pcc_gc_step` returns fine

I reported "the FIRST `pcc_gc_step(1024)` never returns" from a census that
timed out at N=1.  Wrong: the census printed its count and then **the probe kept
running** into the later drain loop, which is what timed out — and on
`subprocess.TimeoutExpired` the harness discards the child's stdout, so the
count was never delivered.  I read "no output" as "no return".

With the census terminating the process right after printing:

```text
strict GENERATIONAL  cap=1  50  5000  200000   ->  nonzero=1 every time
```

The step terminates normally.

### What the mechanism actually is

The census and `pcc_gc_collect` differ in one thing: `pcc_gc_collect` calls
`pcc_gc_begin_explicit_tracing_collect()` first, which sets
`pcc_gc_explicit_collect_active`, and the generational branch of the step gates
its tracing sub-step on exactly that flag.  A census without the flag never
enters the sub-step at all.

Setting the flag around the census, cap=5000:

```text
pcc_python GENERATIONAL   nonzero=5000   <- unbounded
pcc_python INCREMENTAL    nonzero=2         terminates
c          GENERATIONAL   nonzero=1         terminates
c          INCREMENTAL    nonzero=1         terminates
```

Only the strict generational arm reports progress forever, which matches the
hang matrix exactly.  So:

> `pcc_gc_collect` sets `explicit_collect_active` and drains with
> `for (;;) { if (!pcc_gc_step(1024)) break; }`.  On the strict generational
> runtime that flag enables the tracing sub-step, which reports progress on
> every call without ever reaching a terminal state, so the drain loop never
> exits.

This also explains the two sampled hot spots: `+192` and `+304` are the
**return addresses** of the calls to `pcc_gc_generational_step` and the tracing
step cycle, which is where samples land while a callee runs.  Verified by
disassembling the sampled binary — `+192` is the `subs x20, x20, x0`
immediately after `blr` to `_pcc_gc_generational_step`, and the image load
address checks out against `nm` (slide 0x19C000).

### Where the fix goes

The gating predicate is **not** the difference — the strict `_tracing_work_pending`
is a faithful mirror of the C `cycle_requested || mark_active ||
has_sweep_candidate`.  The difference is in the sub-step itself:

```text
C       pcc_gc_step_trace_cycle       reaches a terminal state
strict  pcc_gc_tracing_step_cycle     does not, under backend 3
```

So the strict tracing step cycle keeps returning non-zero without clearing
`cycle_requested` / `mark_active` or draining the sweep candidates that its own
gate tests.

## Nonclaims

- The specific non-clearing statement inside the strict
  `pcc_gc_tracing_step_cycle` is not identified; the two functions are named,
  the diff between them is not read line by line.
- No fix attempted.

### [DENIED] The hang is not caused by a C-extension object being in the heap

The tracing sub-step has a deferral branch for C-extension-tagged objects: it
increments progress, breaks out of the scan, and hands the object to
`_pcc_gc_trace_cext_complete_context`, which only blackens it when a six-part
guard passes.  If that guard fails the object stays grey while the pending slot
is cleared, so the next step finds it grey again and reports progress once more
— an exact fit for unbounded progress, and the list probe is the only one of my
probes whose callback receiver is a C-extension type.

Refuted by injecting a C-extension-typed object into the traced heap of
`collect_during_insert`, which passes on strict generational:

```text
pcc_python GENERATIONAL, cext object present in the dict   no hang
                                                           (fails only on the
                                                            probe's own len
                                                            bookkeeping, 3 vs 2)
```

So merely having a cext object in the traced heap during a callback-driven
collect does not hang it.  What differs in the list probe is that the cext
object is the **receiver of the callback currently executing** — its
`tp_richcompare` is on the stack when the collect runs — not just heap
resident.  Any next attempt should target that distinction, not the presence of
the object.

The injection was reverted; the probe is unchanged.

### [DENIED] Nor is it the cext object being the active callback receiver

The previous denial pointed at the remaining difference: in the list probe the
cext object's `tp_richcompare` is *on the stack* when the collect runs, not just
heap resident.  Tested by giving `collect_during_insert` a C-extension type with
a constant `tp_hash` (so two instances collide and equality actually runs) whose
`tp_richcompare` drives `pcc_gc_collect`:

```text
pcc_python GENERATIONAL, collect driven from inside a cext tp_richcompare
during py_dict_set                                             no hang
```

The probe asserts `eq_collects == 1` and returns its own code otherwise, so this
is not a case of the callback silently never running — the equality did fire and
did collect.

Both denials together move the trigger onto the **list equality scan itself**
(`user_py_list__list_equality_scan`, which is in the sampled stack) rather than
onto the callback, its receiver's type, or the presence of a cext object.  The
next attempt should ask what the strict list scan holds across the callback that
the dict insert path does not.

Both injections were reverted; the probe is unchanged.

### The mark cycle restarts itself — measured

Reading the gray count around each step, with the explicit-collect flag set and
the world otherwise idle:

```text
GRAY k=0 before=0  step=29 after=3
GRAY k=1 before=3  step=3  after=0
GRAY k=2 before=0  step=1  after=3     <- 0 -> 3 with no mutator running
GRAY k=3 before=3  step=3  after=0
GRAY k=4 before=0  step=1  after=3
...  the 0 -> 3 -> 0 oscillation continues indefinitely
```

So the step is not failing to drain — it drains completely (3 -> 0) and then a
**new mark cycle starts**, graying 3 objects again.  Each half of the
oscillation reports progress, which is what makes `pcc_gc_collect`'s
`for (;;)` loop immortal.

Three greys per cycle matches exactly the three registered scheduler roots the
list scan holds across its callback: `list`, `query`, and `candidate`.

A cycle can only restart if `pcc_gc_cycle_requested` is set again.  Both
runtimes' allocation paths set it for backend 3
(`py_gc_backend.py:4011`, `py_gc_backend.c:14851`), and both clear it at the
same guarded mark-cycle-start commit, so neither the setter nor the clearer is
the divergence.  The difference is **what allocates during the step**: the
strict port's own call machinery allocates GC frame nodes, so the step's own
bookkeeping re-requests the next cycle, while the C step allocates no
GC-tracked objects and therefore terminates after one.

That also rehabilitates the frame-node frames in the original sample — they are
not the loop body, but the strict-only allocation that keeps re-arming it.

Fix direction: either an explicit collect's drain must ignore cycle requests
raised by the collector's own internal allocations, or the drain loop needs the
bound that C gets for free by not allocating.

## Nonclaims

- The specific allocation call inside the strict step path that re-requests the
  cycle is **not** identified.  That the strict step allocates and the C step
  does not is inferred from the port's frame machinery and the sampled frames,
  not from a per-allocation trace.
- No fix attempted.

---

## Finding 1 mechanised: the drain loop exits while a sweep is still owed

Same instrumentation technique as finding 2, applied to the failing C arm
(`c-PCC_GC_KIND_INCREMENTAL_TRICOLOR-remove`).  On the C side only
`pcc_gc_has_tracing_sweep()` is readable — `pcc_gc_cycle_requested` and
`pcc_gc_mark_active` are static there, unlike the strict port where they are
real globals, so the extern that worked on strict fails to link on C.

Stepping manually inside the callback:

```text
k=0  sweep=0  -> step=1  sweep=0
k=1  sweep=0  -> step=0  sweep=0    <- step reports no progress
k=2  sweep=0  -> step=6  sweep=1    <- one more step: 6 units, sweep now PENDING
k=3  sweep=1  -> step=0  sweep=1
k=4  sweep=1  -> step=0  sweep=1
k=5  sweep=1  -> step=0  sweep=1    <- pending sweep, never performed
```

`pcc_gc_collect` drains with `for (;;) { if (!pcc_gc_step(1024)) break; }` and
then performs a sweep only `if (pcc_gc_has_tracing_sweep() != 0)`.  At k=1 the
step returns 0 while `has_tracing_sweep()` is still 0, so the loop breaks and
the one-shot sweep check does not fire — **the collect returns while a sweep is
still owed**, and the control cycle is never freed.  One more step would have
produced 6 units of work and raised the sweep flag.

So the defect is the termination condition: a single zero-progress step is
treated as "the cycle is finished", but the collector can legitimately report
no progress for one step while work remains.  This is also why the C arm
terminates at `nonzero=1` in the earlier census — it stops early rather than
looping forever, which is the opposite failure from finding 2.

Fix direction: drain while `step > 0 || has_tracing_sweep() || mark_active`,
rather than until the first zero.  Not yet attempted.

## Nonclaims

- Whether k=1's zero is itself a bug (a step that should have reported the 6
  units it found one call later) is not determined; the drain condition is the
  visible defect either way.
- Not tested on the other three failing C backends; the reading above is from
  INCREMENTAL_TRICOLOR only.

### Why a progress-based drain condition cannot be patched into correctness

The obvious repair — keep draining while `step > 0 || has_tracing_sweep()` —
does not work, and the same reading shows why:

```text
k=1  sweep=0  -> step=0  sweep=0     both signals say "done", but work remains
k=2  sweep=0  -> step=6  sweep=1     the next call finds 6 units
k=3  sweep=1  -> step=0  sweep=1     sweep owed, step reports nothing
```

At k=1 **neither** signal is set while work remains, so any condition built
from them breaks there exactly as the current loop does.  And at k=3 a naive
`while (step || sweep)` would spin forever, since the sweep flag stays set while
step keeps returning 0.

The 0-then-6 shape says the step returns zero at a **phase boundary**: one call
completes a phase without producing countable work, and the next call starts the
following phase.  A drain loop keyed on *progress counts* is therefore
structurally wrong — it cannot distinguish "finished" from "between phases".

The correct condition is collector idleness, which is what the step's own gate
already tests: `!mark_active && !cycle_requested && !has_sweep_candidate`.  In
the C build the first two are file-static in `py_gc_backend.c` while
`pcc_gc_collect` lives in `py_obj.c`, so this needs a small exported predicate
rather than an in-place edit — and the strict port needs the mirror.

Deliberately not attempted in the same turn as the frame-registry fix: that fix
had just been verified across the substrate (240 passed, 9 known-red
deselected), and landing a second GC change on top would make the next failure
ambiguous between them.
