# Investigation: native fragment PCO label publication adds compute work

## Status

active

## Problem Description

The native pointer-reload fragment vertical must preserve Python semantics,
exact PCO output and resource cost before its structural claim is accepted.
The source-frozen v77 pcc1 emits the retained py_ast PIDX identically to v76,
with about 4.3% lower maximum RSS, but two adjacent comparisons show 2.4–3.2%
more CPU and 0.72–0.79% more instructions. The unchanged CLI ASM comparison
is flat. This is a local regression investigation, not the solution to the
remaining 3.01x Stage2/Stage1 goal gap.

Predecessors: `pcc1-indexed-function-kernel-native-data-plane.md` records the
denied scalar-wrapper migrations and required full-record ABI discipline;
`native-fragment-label-record-text-injection.md` establishes the label safety
contract that this work must preserve. Complete v77 receipts and comparisons
are in goal evidence `068-native-reload-fragment-producer.md`.

## Repro

Use `scripts/run_process_tree_sample.py` with its shared performance lock,
60-second watchdog, 6GiB tree cap and `/usr/bin/time -lp` to run the v76/v77
pcc1 binaries with `--pcc-self-backend-indexed-emit-worker`, input
`build/indexed-sidecar-stage2-v58-8g/stage2/pcc2.pcc-pco.17857/module_81.direct.pidx`
and a fresh output path with kind `PCO`. Full commands and terminal rc0
receipts are retained in `build/native-fragment-v77-pyast-{control,candidate}/`
and `build/native-fragment-v77-pyast-repeat-{candidate,control}/`.

## Test [CONFIRMED]

Control/candidate CPU is 14.90/15.37s and 15.38/15.75s; all four PCO files
have SHA256 `2f0f6fa3e03c655403a28b0976efc8f33d6234c07519898125f0e846f257dd56`.
This observation denies acceptance of v77 at present; it does not attribute
all extra CPU to one helper.

## Proposals

- No.1 Retain one narrowed section reference during typed label publication [CONFIRMED locally; full fragment acceptance open].
- No.2 Accept the exact ASCII-identifier subset before extended-symbol validation [pending].

## Attribution before editing

The first native flamegraph sampled PIDX decode and cannot attribute the
changed path. A later native sample, retained under
`build/native-fragment-v77-pyast-emit-profile/`, includes label publication.
Among 4,203 sampled stacks, 53 contain AArch64ModuleBuilder.append_label,
106 contain publish_fragment, and 97 contain AArch64EmissionFragments.
These sets overlap; the partial-window percentages are not whole-worker
Amdahl shares. AArch64ModuleBuilder.finish appears in 2,211 stacks; tail-call
and final-IR attribution must be checked before acting on that larger owner.

The same frozen input under host cProfile records 51,955 fragment publications,
46,625 label records and only 1,714 instruction records. Label validation runs
93,250 times. Receipt/profile: `build/native-fragment-v77-pyast-host-profile/`.
These are caller/count evidence, not native speed evidence.

The full v77 contextual IR contains nine loads of self.current and eight
dynamic attribute operations in _define_label. Its text success path repeatedly
loads is_text, text_error and text_builder. The neighboring append_encoded
method already uses a guarded local current: one self.current load, zero
dynamic attribute operations and direct section-property/builder calls.
Thus the bounded candidate follows an existing narrowed-reference pattern;
it neither removes label validation nor assumes malformed payloads impossible.

## No.1 Narrowed section owner

### Code Change

Pending the contextual red gate: bind current once in _define_label, perform
the existing None check on that local, then use that same section for text
builder/error handling and data symbol offsets. Preserve duplicate labels,
deferred text errors, data visibility, close-on-error and legacy comment rules.

### Pending

Require a red/green full-context dynamic-attribute ratchet, focused label and
driver differentials, and actual production-module native label execution.
A native count-scaled comparison must precede another full Stage1 build.
The complete PCO comparison remains required to accept the fragment vertical;
no v77 Stage2 has been launched.

## Update — narrowed-reference red gate and native prerequisites

The new full-context IR ratchet fails in 52.35s with `('_define_label', 9)`
at `build/native-fragment-label-local-red.log`. The fresh IR is retained in
`build/native-fragment-label-local-red-ir/`. The forward patch changes only
_define_label to retain one guarded local current; all validation, text-error
and data-label behavior remains in the same branches. Focused host label,
driver, structured-emission and fragment tests pass 162 cases in 1.09s.

The new `test_native_label_publication_executable.py` compiles eight actual
production modules with the explicit immutable v76 runtime and then executes
the label path. It covers repeated text labels/instructions, exact first/last
PC offsets, data-section label offsets, final section sizes and malformed
payload rejection/close. Frozen v77-source control and current-source
candidate both pass (18.61/17.58s build-and-test durations, not speed evidence).
Both binaries link only libSystem. The source-pair receipt proves that only
arm64_asm_driver differs across the eight input modules.

Artifacts: `build/native-label-local-{control,candidate}-build/`, including
the source-hashed native canary receipts/binaries and terminal pytest logs;
`build/native-label-local-source-pair.json`. Full-context green and native
count-scaled runtime evidence remain pending. Whole-PCO acceptance is open.

## Update — native label cost and remaining data constructor access

The first patched context completes in 53.01s but is not green: the one-current
load passes and text publication has no dynamic attributes; the data branch
still lowers `TextSymbol(name, current.size, ...)` through one py_obj_getattr.
This is an observed residual, not an accepted exception to the closure claim.
Fresh context: `build/native-fragment-label-local-green-ir/` and its same-name
log. Neighboring append_encoded has zero such calls.

The source-stable native canaries were measured in order control-N,
candidate-N, candidate-2N, control-2N, where N=50,000. All four runs complete
rc0 and print the exact instruction length plus both success/rejection markers.
The process-tree sampler holds the shared lock; no context/test job was live.

| Arm | Labels | CPU | Instructions | Max RSS |
| --- | ---: | ---: | ---: | ---: |
| control | 50,000 | 0.22s | 3,336,251,011 | 39,600,128 |
| candidate | 50,000 | 0.16s | 2,464,257,599 | 39,632,896 |
| candidate | 100,000 | 0.32s | 4,865,339,697 | 73,613,312 |
| control | 100,000 | 0.43s | 6,602,278,207 | 73,564,160 |

The (2N-N)/N instruction cost falls from about65,321 to48,022 (-26.5%).
This directly attributes avoidable native label cost to the repeated dynamic
section reads. Short process timings are rounded to centiseconds; deterministic
instruction deltas and exact output support the local result. It does not prove
whole-PCO acceptance or the Stage2 goal. Receipts:
`build/native-label-local-{control,candidate}-n{50000,100000}/`.

The same proposal will bind the data offset before constructor argument
lowering and re-run the strict zero-dynamic-attribute ratchet. No compiler
semantics or validation rule is changed to remove the residual.

## Update — v78 native compiler and complete PCO comparison

Final context is green:1 passed/53.35s, with one current load and zero dynamic
attribute operations in both _define_label and append_encoded. The final
native label canary passes in18.94s. V78 source
`a60cac12613b6eabb30131f2bf92b4a1daeb1b2ef04c5c604a24aac5bae93ccd`
builds pcc1 successfully in190.88s /746.10 CPU with4.898GB tree peak.
Only arm64_asm_driver differs from the v77 compiler source manifest.
All eight real pcc1 reload/fence/generic-ABI canaries pass in11.68s.

Both directions of the same-input complete PCO comparison preserve the exact
SHA from the original repro. CPU control/candidate is15.55/15.54s and
15.33/15.47s; instruction counts are229.559/230.110B and229.360/230.094B.
RSS remains1.122 ->1.074GB. CPU has no consistent increase in these pairs,
but the remaining +0.24–0.32% instruction signal is not renamed noise or
correctness tax. Receipts: `build/native-fragment-v78-pyast-comparison.json`
and its four linked directories. No Stage2 launched.

## No.2 Exact identifier subset

### Code Change

Proposed: accept a name immediately when both isascii and isidentifier are
true. This subset already satisfies _is_symbol and both separator-normalization
rules. All other names continue through the existing extended-symbol validator,
preserving leading-character restrictions, dot/dollar support and diagnostics.
This avoids the repeated strip, first-character extraction and membership tests
for ordinary generated labels while retaining validation at both publication
boundaries. Do not cache mutable symbol-table contents or remove the second
boundary's malformed-name rejection.

### Pending

Require a finite-character oracle over the previous validator, Unicode and
malformed controls, actual native production label execution/cost, and a new
compiled PCO comparison. The known93,250 validation calls make this an
attributed follow-up for the residual label regression; it remains local
structural qualification, not a proposed solution to the3.01x Stage2 gap.

## Update — ASCII subset qualified locally, v79 frozen

The plain-identifier fast-path ratchet observes red in0.26s, then the grammar,
fragment and owner packet passes23 cases in0.14s. Existing default encoder
tests pass11 with12 integration deselections; full context passes1/52.07s.
Actual eight-module native label execution passes1/19.16s. The compiled
source pair differs only in arm64_encode. Independent review confirms the
strict accepted subset, unchanged extended grammar/diagnostics and both
validation boundaries.

Native control/candidate incremental (2N-N)/N instructions fall48,355 ->43,198
per label (-10.7%). At100,000 labels CPU is0.33/0.26s and maximum RSS
73,580,544/51,134,464 bytes; all four count-scaled outputs are exact. Complete
receipts and gate paths are recorded in goal evidence070-native-label-ascii-subset.

The candidate is frozen as v79 source
`33ab4dd647d50939e89632c352aa6bf44f912a13b7293083fa49874ca6e3a47d`.
Stage1 is running under the established8GiB/360s envelope. Proposal2 remains
pending complete native-worker acceptance; these local results do not prove
the complete fragment migration, Stage2, or fixed point.
