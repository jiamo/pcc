# V79 Stage2 stops at coordinator memory limit

Date:2026-09-06. Status: MEMORY_LIMIT; no pcc2 or Stage3 claim.

The source-frozen v79 full Stage2 run terminates after131.407s with
MEMORY_LIMIT/-15. Process-tree peak8,592,687,104B exceeds the unchanged
8,589,934,592B guard. Largest process is the main pcc1 coordinator at
8,560,541,696B. The final runner manifest is ERROR, and no coordinator
profile or pcc2 was produced. Immediate process checks find no surviving
Stage2/compiler children. Do not treat this as a131s completed Stage2.

Artifacts: `build/native-fragment-stage2-v79/stage2-process.result.json`,
`stage2-process.samples.tsv`, `manifest.json`, and
`build/native-fragment-v79-stage2-run.log`.

The deferred checkpoint contains106 AST files and empty manifests/results;
no final codegen plan exists. Complete export-worker ASTs and a16,138,331-byte
native_exports.json remain under the receipt's private TMPDIR. V76's export
file is15,965,863 bytes, and its coordinator already peaked8,538,537,984B
(tree8,566,816,768B), leaving23,117,824B below the cap. This locates an
existing narrow checkpoint margin; it does not attribute the growth to cp,
JSON, cleanup or any speculative owner.

The exact PCO/native canaries and lower worker memory in evidence070 remain
valid at their boundary. Whole-stage fragment qualification is denied until
the coordinator boundary is localized and corrected. Latest complete Stage2
remains v76=484.762s.

Next: checkpoint-only replay of the existing frozen pcc1 under the same8GiB
cap, with a native flamegraph at the observed final growth. This diagnostic
does not launch codegen/link workers or rebuild a compiler. Investigation:
`docs/investigations/stage2-deferred-checkpoint-coordinator-memory-cap.md`.
No source changes or timeout/memory widening are authorized by this failure.

The parent task retains every open helper/container/text/ASM/verifier family;
no status is promoted from this failed run.
