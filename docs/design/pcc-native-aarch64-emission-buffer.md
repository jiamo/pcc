# Native AArch64 emission buffer

Status: implementation in progress, continuation of native-data-plane closure.
Source baseline: v69, source identity
`41562ebdbabcbe9ffa59726f0837315d35b91527397595cce2f35f4952225206`.
Evidence: [058](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/058-direct-instruction-producer-transport.md).

The core driver and final-layout fixup prerequisites are implemented; native
qualification and remaining boundaries are recorded in
[059](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/059-native-text-buffer-core.md)
and [060](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/060-native-final-layout-fixups.md).
Full producer-buffer closure is still pending.

## Scope and non-claims

Complete one instruction-buffer owner vertically, first on unoptimized PCO:
producer -> native text/label/fixup storage -> final code bytes -> native object.
This is not another opcode helper optimization. The current residual text
inventory is 442,106 of 21,264,800 instructions across 227 retained sidecars;
the much larger hidden projection is the full sequence of placeholders/blanks
through several generic lists even for the 20,822,694 direct words.

PCO closure alone cannot close the parent task: normal large-module ASM workers
and verifier/CFG/def-use projections remain explicit work. No Stage2 time is
predicted from the local result. Source-frozen runtime and end-to-end gates
still decide performance acceptance.

## Requirements

R1. One module-owned native text store, not one Python record per instruction.
Word/data spans, label IDs and relocation/fixup facts have packed representation.
Pointer-bearing symbol names and data blobs have named owning side tables;
they are not copied once per instruction. The storage owner closes on success
and failure and is not shared across overlapping emission scopes.

R2. Final byte offsets are canonical. Instruction width, alignment, labels,
stack-map anchors, inline data and text-section reentry update one layout.
Branch fixups, recursive versus cross-atom calls, compact unwind and precise
stack maps consume that same layout rather than rescanning instruction lines.
Preserve duplicate-label, alignment, range and malformed-directive diagnostics.

R3. The PCO producer chain appends into the native store. No instruction-bearing
helper return list, module `lines`, transport `line_chunks`, driver
`physical_lines`/`text_lines`, or encoder `instruction_lines` remains on this
path. Merely eliminating one of those lists or making it short-lived is partial.
Empty helper containers and transient text formatting stay inventoried as
separate open families; they cannot be renamed cold while normally executed.

R4. Keep one encoding authority. Reuse the existing bit encoders and semantic
validation; do not create another handwritten assembler. During implementation,
a clearly counted adapter may parse a transient unsupported instruction through
the current canonical encoder. Final normal-path producer text count must be
zero before that projection is closed. A zero *final assembler fallback* count
is not equivalent, as the v69 inventory demonstrates.

R5. Preserve exact ASM oracle output, including optimized parsed block ordering,
aliases, explicit shifts, data-section order, visibility and relocations. After
the PCO vertical slice, migrate normal ASM worker publication to bounded
serialization from the native representation. Keep the old complete text API
only for explicitly counted oracle/diagnostic calls, not an uncounted Stage2
lane. No linker, scheduler, GC-barrier or root-elision changes in this slice.

R6. Inventory every normal path. The counter contract distinguishes direct
producer records, transient text encoding, final fallback, and instruction-
bearing generic containers by owner. Tests must reject a new unregistered
class or a new diagnostic adapter used by normal workers. Keep ordinary Python
class, list/dict, identity and GC semantics unchanged.

## Existing owners to migrate

- `self_backend_aarch64_darwin*.py`: producer fragments and final module lines.
- `arm64_asm_driver.StructuredAArch64Module`: existing phase transport shell;
  extend it instead of making an object per instruction.
- `arm64_asm_driver.assemble_lines`: chunk -> physical -> text line remapping.
- `arm64_encode.assemble_text_lines`: packed entries plus residual text and
  word runs. Reuse final encoding rules and relocation normalization.
- `self_backend_precise_stackmaps`: target-final PC/anchor consumers.

## Finite implementation order

1. Introduce and test packed text/layout/fixup storage and its driver consumer,
   using existing structured/text fixtures as exact oracles. Temporary producer
   adapters remain explicitly open, not a completed migration.
2. Convert the complete active PCO producer chain to the append interface;
   remove instruction list transport and shared-placeholder FIFO dependence.
3. Close residual instruction-family parsing with counted producer coverage.
4. Transfer bounded native representation serialization to the normal ASM lane.
5. Run the full representation inventory and actual pcc1 worker differentials,
   then one frozen Stage1/Stage2/fixed-point qualification at the board's proper
   boundary. Final five-GC and broader tasks retain their existing dependencies.

## Gates

- Focused `-x -n0` tests: structured encoding, direct indexed kernel, indexed
  codec, precise stack maps, AArch64 cold paths, native object/assembler parity,
  and record inventory.
- Cases: HFA, cold landing payloads, GC-derived SSA reloads, local/forward and
  cross-atom calls, data-in-code, alignment, section reentry, malformed symbols,
  exception cleanup and overlapping emission rejection.
- Strict contextual closure and generated-function inspection, then actual
  pcc1 ASM/PCO replay and byte equality (not merely `--emit-llvm`).
- Frozen retained `module_81` PCO and `module_1` ASM, followed by complete
  input coverage. Same memory budget, process-tree breaker and performance lock.
- No closure or speed acceptance if a normal-path family remains projected,
  diagnostics differ, outputs fail, or a stable meaningful regression remains.
