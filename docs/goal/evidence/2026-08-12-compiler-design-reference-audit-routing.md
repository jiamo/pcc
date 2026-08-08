# Compiler-design reference audit routing closure

The resolved reference audit in
`docs/investigations/pcc-compiler-design-reference-audit.md` classifies the
requested Cornell, CPython, Go, HotSpot/JVM, GraalVM, and LLVM techniques with
semantic preconditions, exact fallbacks, owner boundaries, counters, and
falsifiable gates.

The task-producing audit is capped at six emitted rows.  The selected finite
rows are:

1. `PERF-P1-INCREMENTAL-MODULE-ACTION-DAG`
2. `PERF-P1-PASS-ANALYSIS-INVALIDATION`
3. `PERF-P1-COMPACT-COMPILER-ARENAS`
4. `PERF-P1-GUARDED-SPECIALIZATION-LOOP-PLAN`
5. `PERF-P2-ESCAPE-SCALAR-MATERIALIZATION`
6. `PERF-P2-CODE-IDENTIFIED-PGO`

The audit's optional long-running runtime tier remains an evidence-only idea.
It does not become a seventh executable row unless a future human task replaces
one of the selected rows or authorizes a separately bounded board expansion.
All six emitted performance rows remain design-gated until their own current,
mode-labeled baselines exist; this closure does not claim that any optimization
has been implemented or measured.

