# Native text buffer core

Status: WIP. Host/core gates passed; current source has not built pcc1 or Stage2.
Updated: 2026-09-05.
Plan: [native emission buffer](../../../design/pcc-native-aarch64-emission-buffer.md).
Predecessor and latest completed timings: [058](058-direct-instruction-producer-transport.md).

## Implemented boundary

One `PackedAArch64TextBuilder` owns packed entry and relocation arenas, final
PC, labels and inline-data metadata. The text oracle and native driver share
its directive parser and one final encoding/relocation implementation.
`assemble_native_text_entries` is a word/data entry boundary, not another
handwritten assembler. It rejects malformed widths, word/data/symbol indices
and alignment while releasing owned arenas on failure.

The driver streams source chunks and splits only multiline chunks. It no longer
constructs `physical_lines` or `_SectionBuffer.text_lines`, and it leaves the
transport's source chunk-index column unchanged. A structured instruction
enters a native entry directly, not a blank slot in another list. Section
reentry shares the same builder; every builder closes on success and failure.

The class inventory now also discovers `arm64_*.py`, classifying the new arena
and existing driver/encoder phase shells. A new unclassified AArch64 record
fails the same source gate; changing the filename cannot conceal it.

## Correctness observations

Streaming exposed a diagnostic-order mismatch before native builds: duplicate
text labels followed by `.bad` reported the duplicate instead of the historical
driver `.bad` error. A minimized regression observed the mismatch. The driver
now retains the first text error per section while completing directive
validation, then raises it at sorted section finalization; no instruction list
was restored. Both malformed cases and existing as(1)/link execution gates pass.

Final focused packet: 204 passed, 1 contextual gate deselected, in 4.27s:
structured encoding, directive driver, remaining Mach-O sections, direct
indexed kernel, precise stack maps, indexed codec and record inventory.
Standalone strict encoder and driver closures passed. The full contextual
gate passed in 52.81s with artifacts under `build/native-text-builder-context`.

The first attempted standalone pcc1 source canary was invalid for this boundary:
its external `pcc.backend` imports were not supplied as a source closure. It
failed in 0.39s with the unhelpful `__init__` diagnostic. Both host and pcc1
emit-only controls produced `No module named 'pcc.backend.arm64_asm_driver'`
and an unavailable `check` stub, so it never executed the new builder. The
failure is not evidence against the builder. The test now uses the supported
native indexed-worker boundary and verifies the chosen compiler's source
manifest against both changed modules before replay. No compiler semantics
were changed to make the invalid single-file harness succeed.

## Open boundaries

The emitter still creates helper/module line containers and placeholder slots.
The residual producer text family still exists. Labels, symbols, data blobs and
explicit text-oracle instructions remain named builder side tables. Normal ASM
worker publication and verifier/CFG/def-use closure are not completed. No
Stage1/Stage2/RSS improvement is claimed for this source until actual pcc1 gates
and representative worker measurements finish. Latest completed Stage1 remains
v69 167.80s; latest complete Stage2 remains v58 544.963s, different source.
