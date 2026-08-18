# Summary-worker coordinator and GC0 fixed point

Task: `PERF-P1-STAGE2-COORDINATOR-IR-STREAMING`

## Frozen implementation

- Source snapshot:
  `build/native-data-plane-stage1-summary-workers-line-codec-v1/source-snapshot`
- Source manifest:
  `c30667c5b06086a20b933c94bb5cd44830047cc40d608c7b7cebe74304c1a4be`
- pcc1:
  `05ef30372935569167ea6a7a6280d9994855b5e225a299ba61cddaabe14b8e1f`
- Runtime archive:
  `build/native-data-plane-stage1-summary-workers-line-codec-v1/runtime-bundle/libpy_runtime_pcc_py.a`

The accepted integration uses one short-lived process per AST, summary
concurrency two, the standalone line codec, a compact effect export surface,
and a parent dense-ID fixed point.  The five production source files were
reapplied by forward patch and are byte-identical to this snapshot.

## Adjacent cache-off Stage2 comparison

Both arms used GC0, frontend jobs 10, self-backend/link jobs 8, disabled IR and
object caches, the shared performance lock, unique outputs and synchronized
process-tree sampling.

```text
metric                    V79 control       summary candidate    ratio
stage wall                708.970 s         578.301 s            0.815692
aggregate CPU             4023.414 s        2953.624 s           0.734109
process-tree peak         14,373,863,424 B  10,569,498,624 B     0.735328
largest process           -                 4,110,974,976 B      < 8 GB gate
pcc2 SHA                  1c62b168...       47724358...           source differs
```

Candidate wall is 1.225953x faster than the adjacent V79 control, not merely
within the pre-registered +5% ceiling.  It produces a runnable pcc2 and links
only libSystem.  Full artifacts:

- `build/native-data-plane-stage2-adjacent-v79-control-v1`
- `build/native-data-plane-stage2-adjacent-summary-candidate-v1`

## GC0 Stage3 fixed point

The candidate pcc2 compiled the same frozen source with the same cache-off
knobs and CPython 3.15.0rc1 as the declared host helper:

```text
stage wall / rc           549.770 s / 0
outer sampled wall        550.568 s
aggregate CPU             2754.044 s
tree / any-process peak   10,893,606,912 B / 4,224,434,176 B
summary jobs/parallel     218 / 2
summary nodes/edges       4,731 / 7,789
pcc2 SHA                  4772435883edb165ad37302197b89de39ca257c6b332d64663baabaf2c534b56
pcc3 SHA                  4772435883edb165ad37302197b89de39ca257c6b332d64663baabaf2c534b56
cmp                       raw byte-identical
linkage/executable        libSystem only / --help rc=0
```

Artifact:
`build/native-data-plane-summary-workers-line-codec-gc0-stage3-v1`.

## Focused gates

```text
summary codec/eager/duplicate/reexport/worker ownership   24 passed
real multi-file reexport/class/typing consumers            3 passed
bootstrap baseline                                         2 passed, 2 deselected
```

## Claim boundary

`[CONFIRMED]`: the GC0/AArch64 coordinator no longer retains all AST object
graphs, stays below the single-process limit, improves adjacent Stage2 wall and
CPU, and reaches a raw pcc2/pcc3 fixed point without libpython or LLVM owner.

Not claimed: Stage2 <= the new uninstrumented CPython 3.15 Stage1, native pcc1
faster than host pcc0, static provenance/parallel emit completion, or GC1--4.
Those are the immediately following performance and five-GC rows.
