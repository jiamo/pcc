# TLA+ model of the container-commit x concurrent-tracer protocol — 2026-08-26

Filed by direct human request during review of the concurrent commit-race
probes: hand-written timing probes kept overclaiming (sticky windows,
unproven overlap), so the protocol gets a formal model instead.

## Deliverables

- Spec: `docs/specs/container-commit-tracer/ContainerCommitTracer.tla`
- Configs: `mc_clean.cfg`, `mc_victim_swap_bug.cfg`, `mc_early_plan_bug.cfg`
- Explorer: `docs/specs/container-commit-tracer/explorer.py`

The spec models one dict owner with an interior window per operation
(`half` = rooted hash-callback restart window, `deleting` = tombstone
window), displaced-value plans that must complete only against a fully
committed owner (`FinishPlan` guard), a concurrent tracer whose sweep
collects only plan-completed values, and mutator-held roots. Mutator
operations allocate FRESH value objects, mirroring the probes.

Invariants: `TypeOK`, `InvNoPrematureFree` (a value live in the entry or
held by the mutator is never finalized), `InvPlanCompletionSeesCommitted`
(every plan-completion event observed a committed owner).

## Verification

The earlier "TLC unavailable" conclusion was wrong: after correcting the
spec's module header, union spelling, action guards and terminal self-loop,
the existing `~/.cache/zdb/formal/tla2tools-1.7.2.jar` parses and checks the
model normally.  Commands were run from `docs/specs/container-commit-tracer`:

```text
java -cp ~/.cache/zdb/formal/tla2tools-1.7.2.jar tlc2.TLC \
  -config mc_clean.cfg ContainerCommitTracer.tla
java -cp ~/.cache/zdb/formal/tla2tools-1.7.2.jar tlc2.TLC \
  -config mc_victim_swap_bug.cfg ContainerCommitTracer.tla
java -cp ~/.cache/zdb/formal/tla2tools-1.7.2.jar tlc2.TLC \
  -config mc_early_plan_bug.cfg ContainerCommitTracer.tla
```

Durable outputs are under `build/test-logs/tlc-container-commit-*.log`.
The clean run reports `Model checking completed. No error has been found`,
62,588 generated states, 22,341 distinct states, zero states left on queue,
and complete depth 17.  The two fault injections each fail the intended
invariant:

- victim swap: `InvNoPrematureFree`, with `v2` simultaneously stored in the
  committed entry and finalized;
- early plan: `InvPlanCompletionSeesCommitted`, with a plan completed while
  the entry phase is `half`.

The independent `explorer.py` enumerates the same corrected transition
relation at Values=6/MaxOps=4/TraceBudget=2:

```text
[clean]            22,341 states  all invariants hold
[victim_swap_bug]  13,998 states  InvNoPrematureFree VIOLATED
    counterexample: v2 live as entry.val while finalized via a
    displaced-plan sweep (the swap displaces the new value)
[early_plan_bug]   41,421 states  InvPlanCompletionSeesCommitted VIOLATED
    counterexample: FinishPlan completed during a "half" window and
    recorded phase="half"
```

The two injections demonstrate the model has teeth: each corresponds to
one defect class the real contract forbids, and each produces exactly
the expected counterexample shape.

## Gap analysis — what the model does NOT prove about the C/strict runtimes

1. Atomicity boundaries: a real `py_dict_set` interior is many machine
   steps; the model collapses it into Start/Commit. Interleavings finer
   than one action are not represented.
2. No memory layout, refcount arithmetic, rehash transaction, table
   relocation or read barriers are modeled; backend 4 behavior is out
   of scope entirely.
3. Exactly-once finalization holds BY CONSTRUCTION here (finalized is a
   set); the real risk lives in C bookkeeping that the model does not
   mirror.
4. Freshness (`v` referenced nowhere) abstracts unbounded allocation;
   the runtime can resurrect a dead address via allocator reuse, which
   no finite-value model represents.
5. Fairness/liveness unmodeled: nothing here proves a collect terminates
   or that a pending sweep eventually runs — safety only.
6. Mode label: results prove properties OF THE MODEL at the recorded
   bounds. They are NOT evidence about pcc backends 0-4 behavior; the
   runtime-facing claims remain with the substrate probes.

## Status vs the task row

The task's model-level exit criteria are satisfied: SANY parsed the spec,
real TLC exhausted the clean state graph without error, both injected bugs
produced the expected counterexample class, and the runtime nonclaims remain
explicit. This is model evidence only, not a five-GC runtime proof.
