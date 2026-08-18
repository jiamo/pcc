# Native-data-plane whole-Stage2 profile — 2026-08-27

Claim level: same-source GC0 no-libpython/self-backend performance baseline and
owner selection. This evidence does not claim that the native data plane is
complete, that Stage2 is competitive with Stage1, or that GC1-4 have
transferred.

## Frozen source and outputs

- source closure:
  `bd27a19dd99fbb8cea687f67a59cdcfd466e6a6be29f76a3a2f0425c3eb01cb2`
- Stage1 compiler:
  `8e94030a10e241a6daf83cff7a966351bb27842a56f911ea3ee372a385389269`
- Stage2 compiler:
  `8f5884dc07538f2f246a1928480e26df6b0b685c685adce20398117847ed2d43`
- the Stage2 compiler is byte-identical to the formal GC0 pcc2/pcc3 fixed-point
  artifact, links only `/usr/lib/libSystem.B.dylib`, and passes `--help`;
- current `self_backend_ir.py`, `self_backend_kernel.py`, and
  `self_backend_prepare.py` are byte-identical to the frozen source used for
  the measurements below.

The source-frozen Stage1 receipt is under
`build/owned-ifexpr-stage1-candidate-v1/`. The complete process-tree Stage2
profile is under `build/native-data-plane-stage2-profile-v1/`. The
caller-attributed item311 profile is under
`build/native-data-plane-item311-flame-final-v2/`.

## Same-source baseline

| metric | Stage1 | Stage2 | interpretation |
|---|---:|---:|---|
| unsampled wall | 260.56 s | 772.453 s | Stage2 is 2.965x Stage1 |
| CPU | 1046.30 s | 4380.95 s | 4.187x, but Stage2 is from the sampled diagnostic run |
| instructions | 293.517 B | 1017.627 B | 3.467x, same diagnostic qualification |
| process-local max RSS | 4.284 GB | 7.246 GB | not aggregate worker memory |
| peak footprint | 1.592 GB | 15.705 GB | Stage2 sampled diagnostic run |
| peak process-tree RSS | not sampled | 11.613 GB | 28 concurrent descendants |

The sampled Stage2 completed in 1036.56 s. The sampler is deliberately not
used as the acceptance wall number; it supplies synchronized process-tree and
instruction evidence. Its receipt reports 2979 samples, return code zero, 215
frontend modules, 485 native objects, zero object-cache hits, and durable
stdout/stderr/time/profile artifacts.

## Whole-stage phase attribution

The complete sampled Stage2 spent:

- 770.168 s / 74.4% in the complete self-backend IR-to-linked-output window;
- 655.280 s / 63.3% in native object emission;
- 562.326 s / 54.3% in safe native emit workers;
- 79.091 s / 7.6% in oversized native emit workers;
- 114.203 s / 11.0% in the owned final linker;
- 186.329 s / 18.0% in parallel frontend code generation;
- 13.032 s / 1.3% preparing the runtime archive.

This rules out the linker and runtime archive as the next dominant owner. It
also shows that worker representation work, not merely scheduling, must fall
before later bounded parallelism can close the Stage2/Stage1 gap.

## Caller-attributed representative worker

The final-source item311 input is 5,108,635 bytes, SHA-256
`76af6689f079d29a5965733c4e7b365c9d4a8ccc16d0ce8a70e21fea6b65468c`.
The 16,830 on-CPU samples attribute inclusive time as follows:

- `prepare_module_for_target`: 86.51%;
- `parse_self_backend_module` / `_parse_functions`: 56.54%;
- `_parse_block`: 53.90%;
- `_parse_instruction`: 42.36%;
- `_parse_call_instruction`: 27.75%;
- `_call_instr_from_parts`: 26.14%;
- verifier: 17.94%;
- call-argument parsing: 15.88%;
- precise stack-map planning: 13.49%;
- stack preparation: 11.41%;
- value-token decoding: 10.91%;
- managed-pointer granule probe leaf: 9.74%;
- indexed-kernel lookup/construction: 8.43% / 6.60%.

Eliminating all `_parse_instruction` work has a 1.735x representative-worker
ceiling. If that share generalized across the 63.3% native-emission phase, its
maximum whole-Stage2 ceiling would be about 1.37x. Eliminating the entire
parser would have a 2.301x worker ceiling and an estimated 1.56x whole-stage
ceiling. These are ceilings, not forecasts. Even the larger one cannot close
the current 2.965x Stage2/Stage1 gap, so the dependent analysis, value-record,
projection-closure, provenance, and bounded-parallel rows remain necessary.

## Object-projection inventory

A performance-lock-held host structural oracle ran the exact item311 through
parse, verification, indexed-kernel construction, stack-slot preparation, and
AArch64 precise-stack-map planning. The host oracle uses `list[int]` behind
`CompilerIntArena`; pcc1 lowers the same source to allocator-backed i64
storage, so those 433,396 scalar list entries are reported separately and are
not classified as compiled scalar payload projection.

Input shape:

- 1 function, 2 arguments, 9474 blocks, 267 phis / 534 incoming edges;
- 59,984 ordinary instructions and 9474 terminators;
- 427,967 instruction operand fields;
- 18,444 indexed values, 59,132 uses, and 9 canonical kernel types;
- 20,004 packed safepoints, 128,108 packed root locations, 5176 packed
  reloads, and 810 location groups.

Explicit lazy-projection counters are green but narrow:

- instruction diagnostic materializations: 0;
- indexed-kernel diagnostic projections: 0;
- safepoint diagnostic projections: 0;
- retained legacy safepoint records: 0.

The reachable parse-to-stackmap graph still contains:

| family | live objects |
|---|---:|
| tuple | 287,605 |
| generic list | 112,635 |
| generic dict | 9485 |
| `TypeDesc` dataclass | 137,468 |
| `SlotInfo` dataclass | 17,471 |
| `ParsedBlock` dataclass | 9474 |
| terminator `ParsedInstr` dataclass | 9474 |
| `AllocaInfo` dataclass | 898 |
| `PhiInstr` / `PhiIncoming` dataclass | 267 / 534 |
| retained `PlannedRootLocation` dataclass | 1125 |
| block-local `CompactParsedInstrArena` | 9474 |

The instruction arena alone retains one generic tuple per ordinary
instruction: 59,984 payload tuples plus 427,967 operand fields. The indexed
kernel still stores its block/value/def-use/last-use/type/register facts in
generic nested lists and dicts. Therefore zero view materialization is not zero
object projection, and the native data plane is explicitly unfinished.

## Verdict [CONFIRMED]

The next coherent representation owner is the packed instruction data plane:
construct opcode/result/type IDs and operand spans at the parse boundary and
carry them directly through verifier, stackprep, stack maps, regalloc, and
AArch64 emission. Keep exact spelling/immediate/diagnostic side tables and an
explicit unsupported adapter.

This selection is based on `_parse_instruction` owning 42.36% of the current
representative worker and on the concrete 59,984 instruction payload tuples.
It is not permission to stop after that slice: the inventory proves that the
shared-analysis and compiler-value projections remain separate large owners.

