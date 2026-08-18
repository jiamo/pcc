# Investigation: Stage2 coordinator exceeds memory cap at deferred checkpoint

## Status

active

## Problem Description

The first complete Stage2 qualification of the native fragment cohort fails
the unchanged8GiB process-tree guard before any codegen worker manifest is
published. Representative PCO execution is exact and uses6.2% less RSS; this
is a distinct coordinator/export/checkpoint ownership boundary.

Predecessors: `pcc1-indexed-function-kernel-native-data-plane.md` records the
denied full-IR handoff, collection-only and repeatedly-decoded AST proposals,
and the accepted summary-worker checkpoint. `native-fragment-pco-label-publication-regression.md`
records the independently qualified fragment/label producer path. Do not
rederive those denied memory explanations or widen the8GiB cap.

## Repro

`run_pcc_stage2_from_receipt.py --stage1-dir build/native-fragment-stage1-v79
--output-dir build/native-fragment-stage2-v79 --stage2-timeout 600
--smoke-timeout 60 --self-backend-jobs 2 --frontend-jobs 7
--max-tree-rss-bytes 8589934592 --prediction-state
build/span-projection-restored-stage2-v76`, under780s outer timeout.

Frozen source:`33ab4dd647d50939e89632c352aa6bf44f912a13b7293083fa49874ca6e3a47d`.
Compiler:`39519f208a31116bad93e654e64974257089efe5bd94d729c1a2c04ebfea1d40`.
Mode: GC0, self backend, no libpython, immutable v76 runtime, native frontend
auto policy, backend2/link8. V79 Stage1 succeeds190.37s;8 real canaries pass.

## Test [CONFIRMED]

The run terminates MEMORY_LIMIT/-15 after131.407s. Tree peak8,592,687,104B;
largest process is coordinator59396 at8,560,541,696B. No pcc2 or completed
coordinator profile exists. The terminal manifest is ERROR. Immediate process
readback finds no surviving v79 Stage2/compiler children.

Evidence:`build/native-fragment-stage2-v79/stage2-process.result.json`,
`stage2-process.samples.tsv`, `manifest.json` and
`build/native-fragment-v79-stage2-run.log`.

## Attribution so far

The deferred state contains106 copied AST files, empty manifests/results and
no final plan. The source export-worker directory retains the completed ASTs,
native_exports_pre_effect.json and native_exports.json. The final export file
is16,138,331 bytes versus v76's15,965,863 (+1.1%). The checkpoint source calls
/bin/cp -R for ASTs, then copies exports and publishes worker manifests.
Partial files locate the boundary but do not prove cp causes the parent peak.

V76 coordinator peak was already8,538,537,984B (tree8,566,816,768B), leaving
only23,117,824B below the tree cap. V79 crosses it before checkpoint completion.
At earlier matching60s samples both coordinators are about6.18GB. No evidence
yet attributes the final growth to fragment storage, JSON serialization,
export expansion, cleanup or process spawn. Recent relevant changed owners
include pipeline_exports free-function/alias expansion, field/static-export
metadata and the new fragment module; label emission is not known to execute
on this coordinator boundary.

## Proposals

- No.1 Capture the existing native coordinator checkpoint stack [pending diagnostic].

## No.1 Checkpoint-only profile

### Code Change

None. Replay the existing frozen pcc1 coordinator with deferred-plan output
under the same8GiB guard, without launching codegen or link workers. Capture
the native call stack near the observed final growth using the repository
flamegraph tool and the sampled process's own executable. Retain all output
and terminal status; do not describe a partial checkpoint as Stage2 success.

### Pending

Use the captured owner to define a cheap focused regression and one bounded
ownership correction before another compiler build or complete Stage2 retry.

## Update — first checkpoint replay and profiler selection gap

The checkpoint-only replay completes rc0 in134.715s, with peak8,531,918,848B.
The last incremental progress message reported7.165GB, but that was not the
terminal peak; the user-facing reading was explicitly corrected after receipt
readback. The completed plan contains228 modules; no codegen/link phase ran.
Profile counters confirm export/summary width2 and completed deferred codegen
publication. Receipt/profile:`build/native-fragment-v79-checkpoint-profile/`.
This remains close to the cap and is not a memory fix or full Stage2 result.

At108s the flamegraph tool followed the supplied coordinator PID to a short
/bin/sh child and failed image validation after that child exited. No valid
flamegraph was produced. A later raw sample attempt found the coordinator
already exited and produced no usable report; no attribution is claimed.

The existing tool now supports --exact-pid for native cpu/heap/peak capture,
preserving executable identity, default child following and host behavior.
The explicit-PID test first fails on the unsupported argument; all21 tool
tests then pass in0.20s, including default child selection, exact coordinator
selection in all three modes and invalid mode/PID rejection. Logs:
`build/flamegraph-exact-pid-{red,final}.log`. AGENTS tool documentation updated.
No compiler/runtime source changed for this diagnostic facility. A repeated
checkpoint diagnostic will use the repaired tool to capture the actual owner.

## Update — exact native attribution

The repaired --exact-pid capture succeeds against the v79 pcc1 itself, with
17,350 symbols and14,094 nonblocked samples. Its checkpoint completes in
138.023s at8,531,230,720B peak; no child survives. Artifacts:
`build/native-fragment-v79-checkpoint-exact-profile/{process.result.json,profile-tool.log,folded.txt,flame.svg}`.
This is diagnostic timing, not a speed acceptance.

Nearest project-owner counts include4,810 samples in export_meta.encode_type,
2,020 in profiled_gc_collect_native_adapter,1,714 in
build_unique_external_class_preload and666 in _native_export_to_wire. Most
encode_type stacks descend from build_unique_external_class_preload_index.
The main allocation owner is therefore class preloading/type serialization,
not a conclusion inferred from the partially copied checkpoint directory.

The source explains a specific duplicate: register_class_type stores the same
ClassType object under its local and module-qualified keys, while
build_unique_external_class_preload calls recursive encode_type and hashes
its newly materialized tuple for every key. Structural dedup happens only
after serialization. The per-root index repeats that owner for sensitive
roots. No cross-root reuse is assumed safe.

## No.2 Reuse each identity's descriptor within one preload [pending]

### Code Change

Proposed function-local map from type identity to retained (type, descriptor
ID). The owning type stays alive with its key. Reuse only the same type object
within the current frozen preload traversal; distinct-but-structurally-equal
types still pass through existing descriptor_ids equality and dedup. Preserve
key iteration order, root-sensitive rebuilding, dependency discovery and all
serialization semantics. No process-global memo or retained cross-root graph.

### Pending

Observe duplicate serialization in a focused real preload test, prove exact
alias/equality/ambiguity/root delta behavior, and measure native cost before
any new full Stage2 run. The profile locates the owner; its memory reduction
remains a hypothesis until a controlled native result exists.

## Update — identity reuse local proof

The real one-class preload test observes two encode_type calls for the same
ClassType before the correction (red0.08s). The forward patch keeps a local
id ->(retained type,type ID) map, requires an exact `is` hit and leaves the
structural descriptor map and key append ordering intact. The25-test
preload/schema/export packet passes in0.22s, including identity collisions,
distinct equal types and the existing ambiguity/own-root/dependency wire cases.
Log:`build/class-preload-identity-host.log`.

The actual v79 function disassembly confirms a direct encode_type call before
the descriptor dictionary lookup. The useful bounded artifact is
`build/class-preload-v79-bounded.disassembly.txt`, function addresses
0x1017a28c4..0x1017a4c0c resolved from that binary's own symbols. The earlier
Mach-O disassembly command ignored symbol filtering and timed out after20s;
its partial output is not used as attribution. No child survived.

Strict current type_infer library emission succeeds within30s and its changed
function has no strict stub. This is emission evidence only. The native cost
gate compiles the exact old/new production function bodies and real
py_ast/export_meta modules; it supplies a prebuilt class map in place of
reconstruction, so it isolates serialization rather than claiming a complete
frontend. Both binaries execute exact nested descriptors/alias IDs and link
only libSystem. Control/candidate builds pass in9.23/8.94s.

Matched source-stable native N/2N runs all complete with exact output:

| Arm | Classes | CPU | Instructions | Max RSS |
| --- | ---: | ---: | ---: | ---: |
| control | 2,000 | 0.16s | 2,525,496,904 | 50,053,120 |
| candidate | 2,000 | 0.10s | 1,488,850,645 | 33,505,280 |
| candidate | 4,000 | 0.23s | 3,417,563,518 | 56,295,424 |
| control | 4,000 | 0.37s | 5,481,737,874 | 89,767,936 |

At4,000 classes instructions fall37.7% and peak RSS37.3%; the native result
supports the local ownership/cost correction. Receipts:
`build/preload-encoding-{control,candidate}-build/` and
`build/preload-encoding-{control,candidate}-n{2000,4000}/`.

The complete host index from the real retained v79 export wire is exactly
equal to the frozen v79 source oracle, including insertion-order JSON bytes:
228 modules,374 types,621 base keys,228 roots/60 nonempty deltas. Index SHA
`f88f005e45c64ff529baa5dd65283eb273bf61d9aca092c39efe0afe82429255`.
Receipt:`build/class-preload-real-wire-differential.json`. The reusable
comparison tool is being added before this is used for subsequent runs.

Whole-coordinator memory remains unproven. Require current-source contextual
qualification, a fresh pcc1 and a checkpoint-only run before a full Stage2
retry; retain the8GiB cap and do not accept the local microbenchmark as closure.
