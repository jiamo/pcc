# Owned IfExpr local transfer — 2026-08-27

Claim level: compiler/frontend ownership correctness through a rebuilt pcc1
and exact self-emitted worker replay. This does not prove complete Stage2,
pcc2/pcc3 fixed point, the Indexed Function Kernel final transfer, or the
five-GC bootstrap matrix.

## Failure and cause

Frozen source `99c047118fa006f4c52cfaad5abe125a518a9f6dcb33a0016e99cfed2013e4f3`
and pcc1 `a9a1a27486ec16eeb26f4d884b7457eb1ad7b9892d8abed2517ab963dadaea20`
reached the first pcc1 -> pcc2 failure after about 330 seconds:

```text
self IR verifier [operand-type] in
OwnershipLoweringMixin__return_type_is_owned_object:
'if.end.2845'/binop expects integer operands
```

The pcc1 frontend emitted four `or ptr` instructions. Host compilation emitted
`or i1`. The pcc1 implementation IR proved that an owned `ct` call result was
selected by an IfExpr, stored into `acc` as borrowed, then released at the loop
step; the dangling, zeroed `ir.Constant` later rendered as `ptr null`.

Both the pre-fix and post-policy pcc1 emitters rejected the same invalid IR in
2.9 seconds, while host-emitted ownership IR passed. This exonerates the
Indexed Function Kernel and verifier.

## Fix

- object-valued IfExpr branches normalize to one owner before the phi;
- borrowed branches retain, already-owned branches transfer;
- the phi is recorded in one emitted-value ownership ledger;
- assignment consumes that record into an owned, rooted, flag-managed local;
- generic dynamic-call/getattr producer records share the same ledger;
- no raw pointer, CPython pointer, scalar or value-payload projection changed.

The ordinary canary was red-first:

```text
current = Canary(7)
selected = current if flag else current
current = Canary(9)
got = selected.tag
```

Pre-fix native output was `<null> 2`; expected output is `7 2`.

## Evidence

```text
host IfExpr IR/runtime plus dyn-attr consumers        8 passed in 8.64s
adjacent root/exact-container/return/consumer packet 40 passed in 14.06s
pcc1 compile+run, independently under GC0/3/4         3 passed in 146.26s
final focused packet                                  11 passed in 151.85s
```

The final focused log is
`build/owned-ifexpr-stage1-candidate-v1/gates/focused-complete.log`.

Strict closed-world ON/no-libpython counts are zero for all five changed
compiler Modules. Their self-emitted assembly sizes are:

```text
_l1_codegen_static_methods     21,419,420
assignment_statement_lowering  11,214,058
control_flow_lowering           2,678,285
host_contract                   2,080,495
ownership_lowering              5,442,837
```

The source-frozen rebuilt pcc1 receipt:

```text
source SHA       bd27a19dd99fbb8cea687f67a59cdcfd466e6a6be29f76a3a2f0425c3eb01cb2
compiler SHA     8e94030a10e241a6daf83cff7a966351bb27842a56f911ea3ee372a385389269
Stage1 wall      260.56s
Stage1 CPU       1046.30s
instructions     293.52B
peak footprint   1.592GB
linkage          libSystem only; no libpython/LLVM
```

Exact module178 replay using the same frozen AST/export sidecars:

```text
old IR SHA       dd7be8cf5f117d05118bbcb1ea1f3214c57588d362750a5ff73c90faf6a34514
old `or ptr`     4
fixed IR SHA     c0717a03fb04b490256749dedaaf7ccfc59bad87e9528efef4ddbd854ed03a3f
fixed `or ptr`   0
fixed asm SHA    8bfaf598290c0eae37ecbff16b52d085f1c8ee36c1ce952c9903598ae695df39
self emit        success
```

The old and fixed artifacts live in separate
`build/owned-ifexpr-module178-{old-replay-v1,replay-v1}` directories. During
the first replay attempt a failed macOS `sed -i` command let the fixed worker
write into the original temporary IR directory; both sides were then
regenerated/copied into the independent directories above before any verdict.

Open boundary: none for this finite ownership slice. Resume
`PERF-P0-INDEXED-FUNCTION-KERNEL`, rebuild/reaccept item311 from the newer
source, and restart Stage2 from a new output directory.
