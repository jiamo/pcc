# Preload identity serialization: local correction qualified

Date:2026-09-06. Status: host/context/native phase passed; fresh compiled
coordinator checkpoint and complete stages remain pending.

## Actual coordinator owner

The exact-PID native profile of the unchanged v79 coordinator succeeds with
14,094 nonblocked samples. Nearest project owners include4,810 samples in
export_meta.encode_type,2,020 in GC collection, and1,714 in
build_unique_external_class_preload. The profiled binary's own bounded
disassembly confirms encode_type before descriptor lookup. Artifacts:
`build/native-fragment-v79-checkpoint-exact-profile/` and
`build/class-preload-v79-bounded.disassembly.txt`.

Both checkpoint-only diagnostics complete near8.53GB, so success alone is not
a margin or memory fix. The failed complete Stage2 in evidence071 remains
the authority for current full-stage status.

## Bounded source change

register_class_type stores one immutable ClassType under short and qualified
keys. The old preload recursively serialized and hashed its descriptor again
for each alias. A real one-class test observes two calls before the patch.

build_unique_external_class_preload now remembers identity ->(retained type,
type ID) for that invocation. An exact `is` hit reuses the ID; each distinct
identity retains the old structural descriptor equality/dedup. Key ordering,
dependency discovery and root-specific rebuilding remain unchanged. The
cache never escapes its preload and keeps its identity keys alive.

Source SHA256 for type_infer.py:
`38fe66f18159607472889affdb202d5e64cb880e551eb6195d7cc4c6f3031b11`.
Independent ownership/semantic review agrees with this boundary.

## Terminal gates and exact real-data result

- Alias/structural equality/identity collision/schema/export packet:
  25 passed/0.22s, `build/class-preload-identity-host.log`.
- Strict standalone type_infer emission succeeds with no stub in the changed
  function: `build/preload-identity-current-closure.{ll,log}`. Emission only.
- Full context:1 passed/52.81s, `build/class-preload-identity-context.log`.
- The actual retained v79 exports produce the same complete host index as
  the frozen v79 source oracle, including insertion-order JSON bytes:
  228 modules/374 types/621 base keys/228 roots/60 nonempty root deltas;
  1,263,280 bytes, SHA256
  `f88f005e45c64ff529baa5dd65283eb273bf61d9aca092c39efe0afe82429255`.
  Readback:`build/class-preload-real-wire-differential.json`; the comparison
  is being promoted to the repository toolset for further replay.

## Native serialization phase

The cost gate compiles the exact old/new production function bodies plus
real py_ast/export_meta modules. Reconstruction is replaced by a prebuilt
class map to isolate the observed serialization owner. This is a phase cost
gate, not the original complete frontend scenario. Both native binaries run
all nested-type and alias-ID assertions, print exact output and link only
libSystem. Artifact roots:`build/preload-encoding-{control,candidate}-build/`.

At4,000 classes control/candidate instructions are5.482/3.418B, CPU0.37/0.23s,
and RSS89,767,936/56,295,424B. At2,000 they are2.525/1.489B and
50,053,120/33,505,280B. The matched N/2N sequence holds the performance lock
and all four outputs are exact. Receipts:
`build/preload-encoding-{control,candidate}-n{2000,4000}/`.

The local cost/memory correction is supported. Complete coordinator memory
must still be measured with a fresh pcc1 under8GiB before retrying Stage2.
Latest successful Stage2 remains v76=484.762s. Full fragment/helper/ASM/
verifier closure and Stage2<=Stage1 remain open.

## Reusable comparator and frozen build

`scripts/pcc_preload_compare.py` now owns baseline function extraction,
real-wire reading, semantic plus insertion-order-byte comparison, drift checks
and exclusive receipt publication.13 focused tests pass in0.52s. Its real
replay returns EXACT and the same index/source/wire hashes:
`build/class-preload-real-wire-tool-differential.json`.

V80 snapshot:`/private/tmp/pcc-preload-identity-v80`.
Bootstrap source SHA256:
`ba03ee455cedf6c2f1fc078a94d2cbbbea84c717f0b701ecb48bff570137ec8c`.
Readiness:`build/preload-identity-v80-readiness.json`.
Pcc/AGENTS editors reported source-stable before freezing. The one changed
compiler owner is type_infer's preload serializer; tool documentation also
changes the source-manifest identity. Stage1 runs with unchanged8GiB guard,
360/410/440s watchdogs and frontend7/backend2/link8 using the immutable runtime.
The measured v79=190.37s supports the160–220s build envelope. No new Stage2
run starts before the fresh compiled checkpoint is qualified.

## V80 compiler readback

Stage1 completes SUCCEEDED in185.70s /736.87 timed-tree CPU, with sampler
peak5,048,434,688B. Compiler SHA256:
`682830c2dad2bf4004ef907c68fad55b8964a89accde84a0e46a2b25bf7c9191`.
All eight real pcc1 reload/fence/generic ABI canaries pass in11.70s:
`build/preload-identity-v80-pcc1-canaries.log`.

A checkpoint-only run is now in flight under the unchanged8GiB cap and160s
diagnostic watchdog, with source/runtime/private paths remapped to the v80
receipt and native frontend auto policy/backend2. It writes a deferred plan
without launching codegen/link workers. Artifact root:
`build/preload-identity-v80-checkpoint/`. Full Stage2 remains withheld until
this terminal result proves adequate coordinator memory margin.

## Compiled checkpoint memory proof and full Stage2 retry

V80 checkpoint completes rc0 in131.773s. Terminal process-tree peak is
7,982,153,728B, versus the two successful v79 diagnostic peaks of
8,531,918,848B and8,531,230,720B. This removes about550MB of observed peak
and leaves607,780,864B below the unchanged8GiB cap. The complete failed v79
Stage2 had exceeded that cap; no peak is inferred from an incremental line.

Native checkpoint output proves exact semantics: native_exports.json is
16,138,331 bytes and byte-identical to v79, SHA256
`6000fb5287c03ce2c8d38c00c1714b4ad5fea52ee9a0602d9890dc047a9b5510`.
The reusable comparison on this new native wire also returns EXACT at the
same index SHA/counts, `build/class-preload-v80-native-wire-differential.json`.
All228 summaries,5,172 nodes and8,677 edges remain; the deferred plan is
complete and no codegen/link worker was run in the diagnostic.

The actual compiled checkpoint now qualifies the memory correction. A single
fresh full Stage2 starts from the successful v80 receipt with unchanged
frontend auto/backend2/link8,8GiB guard,600s stage and780s outer timeout.
Readiness:`build/preload-identity-v80-stage2-readiness.json`.
Output:`build/preload-identity-stage2-v80/`; live log:
`build/preload-identity-v80-stage2-run.log`.
The expected480–560s envelope still uses the completed v76 baseline. Full
Stage2/fixed-point acceptance remains pending; Stage3 must follow Stage2 success.
