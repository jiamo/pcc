# Structural call parser promotion denied

## Proposal

Promote the existing non-regex call parser from fallback to the primary path,
and recognize ordinary ASCII direct/indirect callees without `re.match`.
The proposal did not change call semantics: before implementation, the
existing fallback was compared against `_CALL_RE` over the complete frozen
Stage2 corpus.

```text
modules with calls     416
call instructions      2,678,736
_CALL_RE matches       2,678,736
fallback errors                0
result mismatches              0
```

After implementation, the ASCII callee path was also compared with the cold
quoted/Unicode regex path over the same 2,678,736 calls: zero errors and zero
mismatches. Focused direct, indirect, tail, attribute, aggregate-signature,
quoted, and Unicode call tests passed 10/10; strict self/no-libpython parser
closure emission returned zero.

## Source-frozen pcc1 result

The control is the accepted No.72 CPython 3.15.0rc1 pcc1. The candidate cloned
its 1,137-file snapshot and changed only
`pcc/backend/self_backend_parse.py`.

```text
                         control                           candidate
source     00f912fc97ad19257a96cf73c5f1ea5bb...  3940383b06b4a0bd12145ff71d1e67dd...
parser     809341afa02de5d5c42c6c64d90e6acb...  2bb48a6e784cba5560fbbbce8de7033a...
pcc1       ebde05bbdf2bf0caf47e1f15421de7d5...  2b28360ff1a2a213703fec4adc600a7b...
runtime    624e1de9d6686744906ed3cd0e22cb8de...  identical
mode       CPython3.15rc1, GC0, self/no-libpython, libSystem only
```

Stage1 construction receipts were 274.56s / 177.341B instructions for the
control and 290.05s / 176.611B for the candidate. One sequential construction
per arm is not a performance comparison.

The pre-registered early line required representative item311 speedup at
least 1.05x with improving instructions. Balanced warmups instead reported:

```text
metric                  control             candidate        candidate/control
wall                     15.64 s              15.23 s              0.9738
CPU                      15.53 s              15.16 s              0.9762
instructions        207,258,103,455      209,317,617,264           1.00994
peak footprint        1,702,921,968        1,604,306,576           0.94209
assembly             ff943e10...          ff943e10...               exact
```

The candidate is only 1.027x in wall, 1.024x in CPU, and deterministically
regresses instructions by about 1%. It is denied before paired runs or Stage2.

## Mechanism

A 12-second caller flamegraph makes the cost relocation explicit:

```text
inclusive owner                         control       candidate
regex pattern method                      5.17%          2.38%
extract leading type token                4.61%          8.08%
IR type prefix parser                     4.48%          7.27%
whole call parser                         24.91%         34.34%
```

The structural path removed most regex work but reparsed every return-type
prefix. This is work relocation, not deletion. The candidate was removed by a
forward patch; `pcc/backend/self_backend_parse.py` is byte-identical to the
accepted No.72 source at SHA-256
`809341afa02de5d5c42c6c64d90e6acb9f4dbaa05076d8380c370f2bc7d608b6`.

Artifacts are under `build/call-parser-*`.

