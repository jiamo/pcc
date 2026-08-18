# Native pointer-reload fragment producer: frozen candidate

Date: 2026-09-05. Status: v77 source-frozen Stage1 and actual pcc1 feature
canaries passed; PCO compute regression remains under investigation.

## Complete first vertical

AArch64EmissionFragments owns raw four-i64 word/label records, record-span
roots, a native replay cursor and traced symbol spellings. The three native
packed-stackmap sites now publish explicit fragments; no native_helper_lines,
anonymous placeholder or TypeDesc construction remains in this pointer-reload
chain. Seven append helpers cover slots, address adjustments and streamed
immediate chunks without allocating a fragment per leaf helper.

Existing text helpers remain the exact oracle. Halfword native append is an
explicit unsupported new-helper boundary and rejects before mutation; the
existing halfword text/normal instruction path remains correct and unchanged.
The transport exposes native_fragment_record_count, including words and labels.
The new arena class is registered as a native_arena and remains inventory-visible.

## Failures found before building a compiler

- Host native transport initially called the old packed line methods; real
  reload, large-frame and NOP-position tests were observed red before migration.
- Full context then exposed stale free-function call_sig valueclass shells.
  The generic export repair and a separate renamed-class canonical identity
  repair pass metadata/wire/IR tests and both original failing consumers.
  Existing absent/None signature semantics are preserved.
- Review exposed label payload text injection into append_chunk. Label names
  now validate before interning; publication uses a label-only builder API and
  shares canonical duplicate/PC handling. Corrupted-name rejection and legacy
  text comment normalization are regressed. Independent re-review is clean.

Investigations: `cross-module-valueclass-free-function-call-signature.md`,
`valueclass-renamed-export-canonical-identity.md`, and
`native-fragment-label-record-text-injection.md`.

## Terminal gates

- Label/arena/helper/encoder/driver packet:214 passed/0.52s,
  `build/native-fragment-label-review-final.log`.
- Existing inventory/stackmap/direct-kernel packet:69 passed/2.56s, one full
  context node intentionally run separately; `build/native-fragment-sensitive-packet.log`.
- Full 228-module context:1 passed/47.44s,
  `build/native-fragment-label-final-context.log`; exact IR is retained under
  `build/native-fragment-label-final-context-ir/`. Required new owner/consumer
  bodies have direct aggregate calls, no ValueBox/dynamic call and no strict stub.
- Host-built self/no-libpython executable:1 passed/26.35s using 11 actual
  production modules plus the consumer and the immutable v76 runtime archive;
  `build/native-fragment-canary-label-final-explicit.log`. It executes large
  frame/negative-derived load/add/store, independent fragments/owners, snapshot
  replay, native storage and zero projection. The earlier command without an
  explicit runtime archive skipped and is not execution evidence.

## Frozen build readiness

Source SHA256:
`776ff114d12e260bbc9804b8194fe06c42164f87aaa5ad849b5251a7a85329b9`.
Read-only snapshot:`/private/tmp/pcc-native-fragment-v77`.
Readiness readback:`build/native-fragment-v77-readiness.json`.
All relevant editors are source-stable, and no overlapping performance run is
active. GC0/threads-off, frontend7, backend2, link8, unchanged8GiB cap and
360/410/440s Stage1/guard/outer watchdogs match the retained envelope.
Expected Stage1 capacity is160–220s based on v76's160.98s; this is not a speed
acceptance. The same v76 runtime bundle is reused explicitly.

After Stage1, require source-checked pcc1 feature execution and identical-input
PCO/ASM comparisons against v76. Retain only exact output/diagnostics with no
meaningful CPU/instruction/RSS regression. Full source-frozen stages come after
that worker boundary, never as another diagnostic loop.

The full helper graph, residual producer/instruction text, normal ASM
publication and verifier/CFG/def-use remain open. This pointer-reload vertical
is structural groundwork, not the Stage2<=Stage1 performance solution or parent
task completion.

## v77 native build and executable canaries

Stage1 succeeds in 186.45s / 739.81 timed-tree CPU seconds, preserving the
8GiB guard and the frozen runtime. Compiler SHA256:
`d1fecce6c9f6e61aea380a41c7c0bccc8553aaa14f70b8da425dcd9329ce789b`.
Receipt: `build/native-fragment-stage1-v77/build-receipt.json`.
The eight source-checked pcc1 canaries pass in 11.32s, including all six real
reload/NOP PCO cases, fence ASM/PCO and the generic ABI executable. The expanded
free-function/renamed-valueclass positional/keyword canary separately passes
in 11.24s. Logs:
`build/native-fragment-v77-pcc1-canaries.log` and
`build/native-fragment-v77-free-alias-canary.log`.

## Matched PCO boundary: performance not accepted

Both directions of the adjacent v76/v77 comparison use the exact retained
module_81 PIDX, the same6GiB cap and60s guard. Every arm completes rc0, and
all four PCO files have exact SHA256
`2f0f6fa3e03c655403a28b0976efc8f33d6234c07519898125f0e846f257dd56`.

| Order | v76 CPU | v77 CPU | v76 instructions | v77 instructions |
| --- | ---: | ---: | ---: | ---: |
| control -> candidate | 14.90s | 15.37s | 229.439B | 231.095B |
| candidate -> control | 15.38s | 15.75s | 229.453B | 231.262B |

Process max RSS falls from about1.122GB to1.074GB (-4.3%), but instructions
rise0.72–0.79% and CPU rises2.4–3.2%. The first candidate also has materially
more involuntary context switches; neither wall alone nor memory reduction
waives the repeat's compute signal. This representation is **not yet accepted**.
No v77 Stage2 is launched. Next compare unchanged ASM and attribute the extra
PCO work before changing the representation again.

Receipts: `build/native-fragment-v77-pyast-{control,candidate}/` and
`build/native-fragment-v77-pyast-repeat-{candidate,control}/`.
The latest complete Stage2 remains v76:484.762s, not a v77 result.

## Unchanged ASM control

The frozen module_1 PIDX emits identical ASM under v76 and v77: SHA256
`9811ca4cb92aa9a471743bf845528e7005530b83d8c9af160691c8a44677b8ef`.
Both runs complete rc0 under the same 8GiB cap. Control/candidate wall is
28.42/28.46s, CPU 28.23/28.26s, instructions 398.308/398.249B and max RSS
4.477/4.477GB. Receipts: `build/native-fragment-v77-cli-{control,candidate}/`.
The ASM boundary is flat; the measured PCO increase remains unexplained.

The initial five-second native sample in
`build/native-fragment-v77-pyast-profile/` predominantly covers PIDX decode
(`struct.Struct_unpack_f`), so it does not attribute the fragment regression.
It is a diagnostic artifact, not a speed measurement or a reason to change
the decoder. A later emission-phase sample is required.
