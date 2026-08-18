# Indexed Function Kernel GC0 tracer bullet — formal fixed point

Date: 2026-08-27

Claim level: `DONE_STRONG` for the first AArch64 Darwin/GC0 native-data-plane
tracer bullet only. GC1-4 bootstrap transfer is explicitly deferred behind the
remaining native-data-plane work; this file makes no cross-GC claim.

Frozen identity:

```text
source SHA    bd27a19dd99fbb8cea687f67a59cdcfd466e6a6be29f76a3a2f0425c3eb01cb2
pcc1 SHA      8e94030a10e241a6daf83cff7a966351bb27842a56f911ea3ee372a385389269
runtime/source provenance against snapshot: matched
```

Final item311 bracket:

```text
control before  44.68s / 637.39B instructions / 6.874GB footprint
candidate       30.00s / 407.41B instructions / 4.190GB footprint
control after   43.15s / 636.94B instructions / 6.874GB footprint
assembly        ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251
```

Using the faster control gives 1.438x wall, 1.563x instructions and 39.1%
lower footprint. The normal item311 path creates zero diagnostic instruction
or safepoint projections.

Correctness gates:

```text
fallback + ir.py fallback       42 passed in 556.36s
owned IfExpr focused packet     11 passed in 151.85s
adjacent ownership/root packet  40 passed in 14.06s
changed strict closures         zero fallback + self emit
```

Formal private-cache GC0 chain:

```text
Stage2 result      873.673s, rc=0, cold cache: 0 hits / 485 misses
Stage3 result      390.923s, rc=0, warm cache: 485 hits / 0 misses
pcc2 SHA           8f5884dc07538f2f246a1928480e26df6b0b685c685adce20398117847ed2d43
pcc3 SHA           8f5884dc07538f2f246a1928480e26df6b0b685c685adce20398117847ed2d43
fixed point        RAW byte-identical
linkage            libSystem only; no libpython/LLVM
bootstrap verdict  Self-host gate passed
```

The earlier cache-off Stage3 wrapper budget/recovery is retained in
`2026-08-27-indexed-kernel-gc0-fixed-point-transfer.md` but is not needed for
this formal verdict.

By explicit human priority, incomplete GC1/GC2 matrix processes were
terminated before a stage result and are not evidence. The final frozen native
implementation will run GC0 as reference plus GC1-4 challengers under
`PERF-P0-NATIVE-DATA-PLANE-GC1-GC4-TRANSFER`.
