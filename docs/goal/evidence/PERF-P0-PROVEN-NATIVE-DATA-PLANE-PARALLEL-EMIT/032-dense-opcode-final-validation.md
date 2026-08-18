# Dense opcode-ID final validation — structural win, no Stage2 speedup

## Claim boundary

This slice keeps the end-to-end numeric opcode consumer plane requested by the
human after its first proxy denial.  It also keeps the adjacent fixed-arity
exact-call adapter, which removes transient argument/expected-type lists for
the arity-0/1/2 direct/no-text exact-Function boundary.  Ordinary text,
diagnostic, x86, dynamic-arity, function-pointer and duck-object paths retain
their compatibility projections.

This evidence proves correctness and a local instruction reduction.  It does
not prove a whole-Stage2 speedup, `Stage2 <= Stage1`, fixed point, GC1--4
transfer, or host-Python-free linking.

## Correctness

- Dense opcode reads on the instrumented host module1 path fell from 3,653,049
  to four cold diagnostic projections; host assembly remained exact.
- Dense-ID focused packets passed 29 direct/kernel/inventory, 128 verifier/
  regalloc/stackmap/arena, 301 AArch64, and 15 direct/x86/bootstrap nodes.
- The fixed-call implementation passed 96 focused call/direct/arena/self-
  backend nodes and both changed files passed strict self/no-libpython closure.
- Source-frozen v19 Stage1 succeeded, linked only libSystem, and its function
  canary printed `42`.
- Source-frozen v19 Stage2 succeeded and produced pcc2 `59856bb9...`, linked
  only libSystem.  That pcc2 compiled the function-bearing smoke through
  self/no-libpython and the result printed `42`.

## Local transfer

An adjacent same-machine module1 pair established that dense opcode IDs are a
real local win despite the earlier non-adjacent negative arm:

```text
metric                    v13 control       dense-ID v17       change
wall                         63.61s              61.78s          -2.9%
user + system                63.46s              61.66s          -2.8%
instructions                856.238B            828.776B         -3.2%
cycles                      213.281B            206.510B         -3.2%
peak footprint                6.491GB              6.379GB        -1.7%
assembly                    8a1dd249...          8a1dd249...      exact
```

The fixed-call adapter then removed the generic helper from 93.2% of calls and
reduced its inclusive sample share from 17.13% to about 2%.  Its adjacent v19
versus dense-v17 result was nevertheless CPU/wall neutral:

```text
metric                    dense-ID v17       fixed-call v19     change
wall                         62.21s              62.22s          neutral
user + system                61.90s              61.90s          neutral
instructions                828.856B            818.857B         -1.21%
cycles                      206.811B            205.961B         -0.41%
peak footprint                6.379GB              6.378GB        flat
assembly                    8a1dd249...          8a1dd249...      exact
```

It is retained as part of the scalar data plane at the human's explicit
no-revert direction, not claimed as a wall-time optimization.

## Complete Stage2 result

The final source-frozen v19 run is the authoritative whole-stage verdict:

```text
metric                         accepted v13       v19 candidate       change
Stage1 wall                       212.18s             227.02s          +7.0%
Stage1 coordinator instructions   92.855B              93.229B         +0.4%
Stage2 compile                    364.616s             395.960s         +8.6%
Stage2 total                      380.931s             410.872s         +7.9%
Stage2 user + system            2,254.044s           2,312.621s         +2.6%
Stage2 peak process-tree RSS       22.539GB              22.885GB        +1.5%
```

The candidate therefore fails the whole-Stage2 performance claim.  Large
projection-count deletion did not transfer into whole-stage wall time; the
fixed-call follow-up removes another 1.21% of representative-worker
instructions but no measurable CPU.  Do not present either as the missing
`Stage2 <= Stage1` breakthrough.

Current accepted performance timings remain v13 Stage1 212.18s and Stage2
364.616s compile / 380.931s total.  The retained live source is structurally
newer but has no accepted replacement performance baseline.

## Newly confirmed architecture boundary

Both Stage1 and Stage2 execute the pcc-owned Mach-O linker through
`PCC_HOST_PYTHON scripts/pcc_link_macho.py`.  Current v19 phase times are
71.259s in Stage1 and 66.754s in Stage2.  This is an explicit transitional
host execution owner: it does not invalidate the libSystem-only/no-libpython
artifact claim, but it blocks host-Python-free bootstrap ownership and is a
common ~67s floor rather than the cause of the Stage2/Stage1 delta.

Artifacts:

- `build/no102-dense-opcode-stage1-candidate-v17/`
- `build/no102-dense-opcode-stage2-candidate-v17/`
- `build/no103-fixed-call-stage1-candidate-v19/`
- `build/no103-fixed-call-stage2-candidate-v19/`
- `build/no103-v17-control-r3.time`
- `build/no103-v19-module1.time`
- `build/no103-v19-cpu-full.folded`

Stage3 and GC1--4 did not run.  The human requested a pause after this final
validation slice.
