---- MODULE ContainerCommitTracer ----

(* Models the container-callback commit protocol of GC-P0-CONTAINER-
 * CALLBACK-MUTATION-COMMIT at protocol level: one dict owner mutated by a
 * mutator thread whose operations pass through an interior window
 * (allocating hash callback / graph-lock tenure), displaced-value plans
 * that must complete only against a fully committed owner, and a
 * concurrent tracer plus inline refcount finalization.
 *
 * Atomicity boundaries (see the evidence file's gap analysis):
 *  - A mutator op is split into a Start step and a Commit step; anything
 *    the tracer or an inline finalizer does between them is the interior
 *    race the real probes exercise.
 *  - The tracer's pcc_gc_step and stop-the-world pauses collapse into one
 *    atomic SweepCollect/TraceStep step.
 *  - Relocation/read barriers are NOT modeled; backend 4 behavior is out
 *    of scope.
 *
 * Bug-injection constants give the model teeth: with the clean
 * configuration every invariant holds; flipping either injection makes
 * TLC produce the corresponding counterexample (see mc_*.cfg).
 *)
EXTENDS Integers, FiniteSets

CONSTANTS Values,          (* the value objects the probe allocates *)
          MaxOps,          (* bound on committing mutator operations   *)
          TraceBudget,     (* bound on pure tracer ticks               *)
          InjectVictimSwapBug,  (* TRUE: CommitReplace displaces the NEW
                                  (now live) value instead of the old   *)
          InjectEarlyPlanBug    (* TRUE: FinishPlan ignores the commit
                                  guard, completing plans mid-operation *)

VARIABLES
    \* The single dict owner's slot.  "half" is the interior of an insert
    \* or replace (rooted callback restart window); "deleting" is the
    \* tombstone window of a delete.  val is the value a reader would see;
    \* new is the incoming value of an in-flight replace.
    entry,
    \* Values rooted by the mutator itself (stack/pinned) while in flight.
    held,
    \* Displaced values removed from the owner whose release plan has not
    \* necessarily completed.
    garbage,
    \* Displaced values whose release plan has completed (unlock passed).
    planDone,
    \* Finalized values.
    finalized,
    \* What the last plan-completion event observed of the owner:
    \* "none" initially, else the owner phase at that moment.
    observed,
    opsLeft,
    traceLeft

Phase == {"half", "committed", "deleting"}

NoneVal == "NoneVal"
ValOpt == Values \cup {NoneVal}

TypeOK ==
    /\ entry \in [occupied: BOOLEAN, phase: Phase, val: ValOpt, new: ValOpt]
    /\ held \subseteq Values
    /\ garbage \subseteq Values
    /\ planDone \subseteq Values
    /\ finalized \subseteq Values
    /\ observed \in {"none"} \cup Phase
    /\ opsLeft \in 0..MaxOps
    /\ traceLeft \in 0..TraceBudget

InvNoPrematureFree ==
    \* A value a reader can obtain from the owner, and a value the mutator
    \* holds, is never finalized.
    /\ (entry.occupied => entry.val \notin finalized)
    /\ held \intersect finalized = {}

InvPlanCompletionSeesCommitted ==
    observed \in {"none", "committed"}

Init ==
    /\ entry = [occupied |-> FALSE, phase |-> "committed",
                val |-> NoneVal, new |-> NoneVal]
    /\ held = {}
    /\ garbage = {}
    /\ planDone = {}
    /\ finalized = {}
    /\ observed = "none"
    /\ opsLeft = MaxOps
    /\ traceLeft = TraceBudget

(* Mutator operations allocate FRESH value objects, mirroring the probe
 * (each round constructs a new instance); a start action therefore
 * requires a value that nothing references yet. *)
StartInsert(v) ==
    /\ v \in Values
    /\ opsLeft > 0
    /\ v \notin (garbage \cup finalized \cup held \cup {entry.val})
    /\ entry.occupied = FALSE
    /\ entry.phase = "committed"
    /\ entry' = [occupied |-> FALSE, phase |-> "half",
                 val |-> v, new |-> NoneVal]
    /\ held' = held \cup {v}
    /\ UNCHANGED <<garbage, planDone, finalized, observed, opsLeft, traceLeft>>

CommitInsert(v) ==
    /\ entry.phase = "half"
    /\ entry.occupied = FALSE
    /\ entry.val = v
    /\ v \in held
    /\ opsLeft > 0
    /\ entry' = [occupied |-> TRUE, phase |-> "committed",
                 val |-> v, new |-> NoneVal]
    /\ held' = held \ {v}
    /\ opsLeft' = opsLeft - 1
    /\ UNCHANGED <<garbage, planDone, finalized, observed, traceLeft>>

StartReplace(v) ==
    /\ entry.occupied
    /\ entry.phase = "committed"
    /\ opsLeft > 0
    /\ v \in Values
    /\ v \notin (garbage \cup finalized \cup held \cup {entry.val})
    /\ entry' = [occupied |-> TRUE, phase |-> "half",
                 val |-> entry.val, new |-> v]
    /\ held' = held \cup {v}
    /\ UNCHANGED <<garbage, planDone, finalized, observed, opsLeft, traceLeft>>

CommitReplace ==
    /\ entry.phase = "half"
    /\ entry.occupied
    /\ entry.new \in Values
    /\ entry.new \in held
    /\ opsLeft > 0
    /\ (IF InjectVictimSwapBug
        THEN   (* displaces the just-committed, now-live value *)
           /\ garbage' = garbage \cup {entry.new}
           /\ entry' = [occupied |-> TRUE, phase |-> "committed",
                        val |-> entry.new, new |-> NoneVal]
           /\ held' = held \ {entry.new}
        ELSE   (* displaces the previous value; the new one goes live *)
           /\ garbage' = garbage \cup {entry.val}
           /\ entry' = [occupied |-> TRUE, phase |-> "committed",
                        val |-> entry.new, new |-> NoneVal]
           /\ held' = (held \ {entry.new}))
    /\ opsLeft' = opsLeft - 1
    /\ UNCHANGED <<planDone, finalized, observed, traceLeft>>

StartDelete ==
    /\ entry.occupied
    /\ entry.phase = "committed"
    /\ opsLeft > 0
    /\ entry' = [occupied |-> TRUE, phase |-> "deleting",
                 val |-> entry.val, new |-> NoneVal]
    /\ UNCHANGED <<held, garbage, planDone, finalized, observed,
                  opsLeft, traceLeft>>

CommitDelete ==
    /\ entry.phase = "deleting"
    /\ opsLeft > 0
    /\ garbage' = garbage \cup {entry.val}
    /\ entry' = [occupied |-> FALSE, phase |-> "committed",
                 val |-> NoneVal, new |-> NoneVal]
    /\ opsLeft' = opsLeft - 1
    /\ UNCHANGED <<held, planDone, finalized, observed, traceLeft>>

(* Unlock passed: the owner is fully committed, so the displaced-value
 * plan finishes and the value becomes collectable.  With
 * InjectEarlyPlanBug the guard is missing -- the exact defect class the
 * contract forbids. *)
FinishPlan(v) ==
    /\ v \in garbage \ planDone
    /\ (InjectEarlyPlanBug \/ (entry.phase = "committed"))
    /\ planDone' = planDone \cup {v}
    /\ observed' = IF entry.phase = "committed"
                   THEN "committed" ELSE entry.phase
    /\ UNCHANGED <<entry, held, garbage, finalized, opsLeft, traceLeft>>

TraceStep ==
    /\ traceLeft > 0
    /\ traceLeft' = traceLeft - 1
    /\ UNCHANGED <<entry, held, garbage, planDone, finalized, observed,
                  opsLeft>>

(* Concurrent-tracer sweep: collects a value whose plan is complete.
 * Only planned values are candidates -- the collector never touches a
 * value the mutator still holds or that is live in the owner. *)
SweepCollect(v) ==
    /\ v \in planDone \ finalized
    /\ finalized' = finalized \cup {v}
    /\ UNCHANGED <<entry, held, garbage, planDone, observed,
                  opsLeft, traceLeft>>

(* The finite model has deliberately exhausted every operation and tracer
 * budget.  Make that successful quiescent state explicit so TLC's deadlock
 * checker distinguishes bounded completion from an in-flight protocol
 * deadlock. *)
Done ==
    /\ opsLeft = 0
    /\ traceLeft = 0
    /\ entry.phase = "committed"
    /\ held = {}
    /\ planDone = garbage
    /\ finalized = planDone
    /\ UNCHANGED <<entry, held, garbage, planDone, finalized, observed,
                  opsLeft, traceLeft>>

Next ==
    \/ \E v \in Values: StartInsert(v)
    \/ \E v \in Values: CommitInsert(v)
    \/ \E v \in Values: StartReplace(v)
    \/ CommitReplace
    \/ StartDelete
    \/ CommitDelete
    \/ \E v \in garbage \ planDone: FinishPlan(v)
    \/ TraceStep
    \/ \E v \in planDone \ finalized: SweepCollect(v)
    \/ Done

Spec == Init /\ [][Next]_<<entry, held, garbage, planDone, finalized,
                         observed, opsLeft, traceLeft>>

=============================================================================
