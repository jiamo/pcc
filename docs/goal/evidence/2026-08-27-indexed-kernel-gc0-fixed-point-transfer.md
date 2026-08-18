# Indexed native data plane GC0 transfer checkpoint — 2026-08-27

Claim level: final-source item311 acceptance, complete fallback gates, formal
GC0 Stage2, and a byte-proven GC0 pcc2/pcc3 artifact fixed point recovered
after the Stage3 wrapper budget expired post-link. No Stage3 wall claim and no
GC1..4 matrix claim are made here.

## Frozen inputs

```text
source SHA    bd27a19dd99fbb8cea687f67a59cdcfd466e6a6be29f76a3a2f0425c3eb01cb2
pcc1 SHA      8e94030a10e241a6daf83cff7a966351bb27842a56f911ea3ee372a385389269
runtime       build/owned-ifexpr-stage1-candidate-v1/runtime-bundle/
              libpy_runtime_pcc_py.a
runtime/source provenance against frozen snapshot: matched
linkage        libSystem only; no libpython/LLVM
```

## Final item311 bracket

| arm | wall | CPU | instructions | footprint | assembly |
|---|---:|---:|---:|---:|---|
| control before | 44.68s | 44.31s | 637.39B | 6.874GB | `ff943e10...` |
| candidate | 30.00s | 29.89s | 407.41B | 4.190GB | `ff943e10...` |
| control after | 43.15s | 43.02s | 636.94B | 6.874GB | `ff943e10...` |

Conservative faster-control ratios: 1.438x wall, 1.439x CPU, 1.563x
instructions; footprint falls 39.1%. The 1.25x worker gate remains accepted on
the final source after the ownership correctness fix.

## Correctness transfer

```text
fallback + ir.py fallback complete gate     42 passed in 556.36s
owned IfExpr final focused packet           11 passed in 151.85s
adjacent ownership/root packet              40 passed in 14.06s
five changed ownership Modules              zero fallback + self emit
```

GC0 Stage2, cache disabled:

```text
stage result wall       772.453s
compile wall            756.181s
publish barrier          16.218s
compile_python_total    755.112s
native emit             468.987s
pcc-owned link           98.207s
pcc2 SHA                8f5884dc07538f2f246a1928480e26df6b0b685c685adce20398117847ed2d43
```

Stage3 wrapper budgets of 720s and 960s were both too small. The second run
completed all 485 non-empty assembly/result pairs and the owned linker produced
`pcc3.tmp` before the parent was killed. Preserved recovery evidence:

```text
pcc3 timeout artifact SHA  8f5884dc07538f2f246a1928480e26df6b0b685c685adce20398117847ed2d43
pcc2/pcc3 raw cmp           equal
pcc3 linkage                libSystem only
pcc3 --help                 pass
pcc3 self/no-libpython smoke compile+run: 42
```

The timeout artifact was copied without byte change to
`build/indexed-packed-record-fixed-point-v3-gc0/pcc3`. This is direct artifact
recovery, not a fabricated `PCC_BOOTSTRAP_STAGE_RESULT`; no successful Stage3
wrapper or wall-time claim is made.

Open boundary: run the final GC0..4 cache-enabled correctness matrix on this
same source/runtime/pcc1, requiring ordinary Stage2/Stage3 completion receipts
and raw or normalized fixed points. Do not compare cold/warm matrix timings as
collector performance.
