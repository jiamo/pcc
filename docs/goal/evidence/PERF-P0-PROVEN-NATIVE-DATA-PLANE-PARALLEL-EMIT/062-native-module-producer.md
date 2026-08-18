# Native module producer bridge

Status: module-level native producer qualified; parent remains IN_PROGRESS.
Task: PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE.
Date: 2026-09-05.
Predecessor: [061](061-incremental-module-builder.md).

## Implemented boundary and remaining work

Unoptimized structured PCO emission now finalizes native sections directly.
The returned transport has no module instruction `line_chunks` and no encoded
line-coordinate arena. The module scope consumes function output immediately,
clears the captured-word arena after each function, and shares the builder's
final labels/PC with stack maps, unwind ranges and branch/call fixups. Existing
opcode encoders, marker finalization, directives and section normalization remain
the rule sources. Both ordinary and fresh-process production workers consume
the finalized sections without rerunning the directive driver.

The three-function regression proves empty module line/coordinate buffers,
per-function capture reuse, and exact complete Section/relocation equality.
The new scope is a registered `phase_shell`; its scratch/capture storage is in
registered integer arenas. The inventory separately names residual native-path
encoder lines, rather than treating empty module lines as a zero fallback count.

Function/helper lists and placeholders, transient producer text, compact-unwind
data directive lists, ASM worker publication and verifier/CFG/def-use projections
remain. This slice does not satisfy the entire instruction-buffer title. No
Stage2, fixed-point, GC1..4 or compiler speed claim is made.

## Focused and contextual evidence

The new finalized-transport test failed before implementation (missing
`native_finalized`, 1 failed in 0.12s). The final focused packet is 212 passed,
1 contextual gate deselected in 3.28s, covering structured encoding, directive
driver, direct indexed kernel, precise stack maps, cold paths and both record
and structured-instruction inventories.

The original contextual assertions passed, but inspecting generated IR caught
a dynamic `get4_unchecked` call on the borrowed capture field. The new IR
ratchet failed against that retained output. Explicit source field declarations
now preserve the existing arena's four-i64 native method ABI; no frontend or
ordinary class semantics were changed. The strengthened contextual gate passes
in 46.52s and checks both direct aggregate calls and absence of unavailable
stubs. Generated IR/log: `build/native-module-producer-context-typed/` and
`build/native-module-producer-context-typed.log`.

The public module append allocation-error cleanup follow-up from 061 is included.
Its injected MemoryError regression was red before the close-on-failure branch,
then the 138 driver/structured tests passed. EncodeError remains deferred to
preserve the established driver-before-text diagnostic priority.

The real retained py_ast inventory completed with 204,827 structured
instructions: 203,033 direct, 1,794 transient text-encoded, zero final fallback.
This is one input's coverage, not a full population claim. Receipt:
`build/native-module-producer-pyast-inventory.json`.

## Frozen native gate

Source SHA256:
`8202e4ed7d60935af7b94452d8e17139e2bbad31f6342f0f5f47345162210ce0`.
Snapshot: `/private/tmp/pcc-native-module-producer-v73`.
Readiness: `build/native-module-producer-v73-readiness.json` (identity matches
the real-input inventory).

v73 uses v72's frozen runtime, GC0/threads-off, 7 host workers, 2 self-backend
workers and 8 link workers. Expected Stage1 is 165–205s; watchdogs are
360/410/440s for build/tree/shell. The shared-lock sampler applies an 8GiB
aggregate-RSS breaker and 2GiB launch reserve. Artifacts:
`build/native-module-producer-v73-build-guard/` and
`build/native-module-producer-stage1-v73/`.

After publication, the source-checked native canary must cover all six changed
production files, followed by actual PCO/ASM differentials and bounded paired
worker costs. A host-green result or emitted IR alone cannot close this gate.

## Native correctness observations

v73 is SUCCEEDED. pcc1 SHA256:
`b0212570b9a51777ba86e805b2a6194309505e903988b04e82bdde0299abfd05`.
Stage1 is 165.83s / 674.49 tree CPU seconds; the outer guard is COMPLETE/rc=0
at 177.83s with a 5,068,767,232B sampled peak. Linkage is libSystem-only and
the function compile/run smoke prints 42.

All three source-checked native worker tests passed in 0.42s: text-buffer
ASM/PCO, HFA and cold landing, with exact output comparisons. Artifacts:
`build/native-module-producer-v73-canary`. No pcc1 rebuild was spent on the
earlier untyped aggregate-return field: that boundary was caught in IR first.

The adjacent v72/v73 real py_ast PCO replay is COMPLETE/rc=0 in both arms,
under the same 6GiB cap and 60s watchdog. PCO SHA256 remains exactly
`2f0f6fa3e03c655403a28b0976efc8f33d6234c07519898125f0e846f257dd56`.
Wall is 16.33→15.91s, CPU 16.21→15.88s, instructions 250.668→245.592B
(-2.03%), sampled tree peak 1,371,406,336→1,346,568,192B, and process-local
maximum RSS 1,380,958,208→1,351,925,760B (-2.10%). This is a bounded
structural improvement, not the solution to the full Stage2/Stage1 gap.
Artifacts: `build/native-module-producer-v73-pyast-{control,candidate}/`.

The adjacent CLI ASM controls also complete with exact SHA256
`9811ca4cb92aa9a471743bf845528e7005530b83d8c9af160691c8a44677b8ef`.
Wall is 29.23→29.17s, CPU 29.18→29.12s, instructions
429.190→429.309B (+0.028%), sampled tree peak
4,416,782,336→4,416,634,880B. This path is effectively unchanged.
Receipts: `build/native-module-producer-v73-cli-{control,candidate}/`.

The finite module-buffer deletion is qualified for continued structural work.
Function/block/helper output lists and placeholders are still normal-path
representations. They, residual text and verifier families prevent task closure.
