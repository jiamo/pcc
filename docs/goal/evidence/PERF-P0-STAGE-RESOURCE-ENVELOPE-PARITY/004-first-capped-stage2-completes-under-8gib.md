# 004 — first current-source Stage2 completes under the 8 GiB cap with a runnable pcc2

Date: 2026-09-03.  Human authorization: a second capped Stage2 with the
timeout at 1500 s (cap unchanged at 8 GiB) after the fixes of evidence 003/004
(PY-P0 ledger).  Source v18 = HEAD e63c5d64 + deferred-lane floor admission +
assembler chunks/join + `bytes.join` runtime + struct zero-copy + str `+=` /
`len()` ownership fixes.

## Same-envelope pair (one 8 GiB process-tree cap, cache-off, frozen v18)

```text
Stage1 v8   build/inline-edge-stage1-capped-v8      rc 0   wall 164.39s  user 757.95s
            peak tree 5.01 GB (28 procs)  libSystem-only  canary 42  pcc1 sha f2a8d9a2
Stage2 v4   build/inline-edge-stage2-capped-v4      COMPLETE rc 0  elapsed 1350s
            peak tree 7.28 GiB < 8 GiB   PCC_BOOTSTRAP_STAGE_RESULT stage=2 elapsed_ms=1349675 rc=0
            pcc2 210782744 bytes, links only /usr/lib/libSystem.B.dylib
            pcc2 --help rc 0; pcc2 compiles the Stage1 function smoke (13.7s) and it prints 42
same-resource Stage2/Stage1 wall ratio: 1350 / 164.39 = 8.2x  (host pcc0 frontend auto=10
vs compiled frontend auto->2; schedulers were free to admit different widths inside the
same cap, as the row allows)
```

The receipt tool `run_pcc_stage2_from_receipt.py` raised
`AttributeError: 'Namespace' object has no attribute 'frontend_jobs'` in
`_resource_envelope` AFTER the stage completed (the envelope-recording code from
evidence 001 had never executed past a completed Stage2 before), so
`stage2-record.json` and the tool's own pcc2 smoke were not written.  The
process-tree receipt, the bootstrap stage result line, the link profile, the
admission receipt and the manual pcc2 canary above are the evidence; the tool
is fixed in this slice (see below).

## Timeline (tree RSS, 30 s buckets)

```text
  0-150s   coordinator 2.8 -> 5.78 GiB, tree peak 6.59 GiB (export workers)
150-225s   serial: cli_bootstrap 6.14 GiB alone
225-330s   paired: pairs <= 6.26 GiB
330-450s   heavy: <= 5.78 GiB       450-570s medium: <= 3.96 GiB
570-1230s  small (193 modules): tree 3-7.28 GiB, 4 workers, floors admitted,
           3 suspensions / 3 resumes, 1712 denied polls; peak 7.28 GiB at 1110s
1230-1350s link driver (assemble 31 .s 37.8s, decode 193 .pco 4.6s, prepare_link 41.8s, total 86.4s)
```

## Per-lane admission (`pcc2.pcc-codegen-plan.admission.json`)

```text
lane             width launched denied susp peak_live peak_charged wall_sum max_wall max_peak
serial             1      1        0     0    6.14G     6.14G        73s     73s    6.14G
paired_oversized   2      6        0     0    6.23G     6.23G       218s     41s    4.00G
heavy              2      8        0     0    5.75G     7.73G       211s     46s    4.23G
medium             3     16      449     0    4.09G     7.00G       265s     30s    2.72G
small              4    193     1712     3    7.26G     8.25G      1772s     46s    4.88G
sum of worker walls 2540s; small lane median peak 1.20 GiB, p90 2.79 GiB
```

Largest workers (module, AST MB, peak GiB, wall s):

```text
cli_bootstrap (serial)                       13.89   6.14   73
py_frontend.py_ast (small, native object)     0.27   4.88   46   <- 18 GiB per AST MB
backend.arm64_encode (small)                  1.99   4.70   37
codegen.runtime_abi (heavy)                   3.21   4.23   46
codegen.ir_scaffold_lowering (small)          1.91   4.18   32
codegen.class_gen (paired)                    7.96   4.00   41
type_infer (paired)                           6.79   3.87   41
```

45 of 224 workers exceeded their AST-derived floor; the ladder covered them
(3 suspensions, no trip).  The small lane still pays the in-process
native-object path: `py_ast` is a 0.27 MB AST that emits many generated
dataclass methods, so AST bytes is the wrong proxy for it.

## Claims and non-claims

- PROVEN: default-safe current-source Stage2 below 8 GiB with a runnable,
  no-libpython, self-backed, libSystem-only pcc2 (HARNESS-P0 exit #4 in
  substance; the formal tool receipt is missing because of the tool bug).
- PROVEN: same-resource paired wall ratio 8.2x (labeled: cache-off, frozen
  v18, host Stage1 vs compiled Stage2, one 8 GiB cap).
- NOT met: the 600 s Stage2 contract (1350 s).  Wall owners by lane sum:
  small 1772 s of 2540 s worker-seconds; the coordinator 150 s; link 86 s.
- NOT run: Stage3 / pcc2->pcc3 fixed point, GC1-4.
