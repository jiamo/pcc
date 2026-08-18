# Native label section owner: v78 prerequisite

Date: 2026-09-05. Status: narrowed label publication qualified locally;
source-frozen v78 Stage1 running, complete PCO acceptance pending.

## Attribution and correction

The retained v77 PCO regression was localized with actual pcc1 sampling,
same-input host caller counts and real contextual IR. The input produces
46,625 labels and 1,714 instruction records. New _define_label repeatedly
loads a nullable section field, creating nine self.current loads and eight
dynamic attribute operations. The neighboring append_encoded method already
demonstrates the single guarded local pattern.

The correction retains one current section and one data offset before
TextSymbol construction. It preserves name validation, duplicate labels,
deferred text errors, data visibility/offsets and close-on-error behavior.
The initial red context fails with nine loads; the first patch removes the
text-path calls but exposes one data-constructor dynamic lookup. The final
same-proposal patch removes that remaining lookup without changing codegen.

Investigation: `docs/investigations/native-fragment-pco-label-publication-regression.md`.

## Qualification

- Host label/driver/structured/fragment packet:162 passed/0.55s,
  `build/native-fragment-label-local-offset-host.log`.
- Fresh full context:1 passed/53.35s,
  `build/native-fragment-label-local-offset-green.log`; actual IR is retained
  under the same stem ending in `-ir/`. _define_label and append_encoded each
  have one current load, zero dynamic getattr/setattr and no stub. Text/data
  calls are direct; TextSymbol receives the data offset as native i64.
- Actual eight-module self/no-libpython executable:1 passed/18.94s,
  `build/native-label-local-offset-build/pytest.log`. Text PCs, instruction
  lengths, data label offsets and malformed-name rejection/close all execute.
  Binary SHA256: `cf5a1b1daf2ee5c06d2e4f2bde0f44981c17302b53681e23a138b0d04603bc11`.

The preceding one-current variant has matched native N/2N evidence against
the frozen v77 label implementation: incremental instruction cost falls
65,321 ->48,022 per label (-26.5%), with exact output and flat RSS. At100,000
labels CPU is0.43 ->0.32s. The final extra local addresses only the data
constructor boundary (two calls in that canary). This is local cost attribution;
it does not establish complete PCO acceptance or explain all v77 extra CPU.

## Frozen next boundary

Source SHA256:
`a60cac12613b6eabb30131f2bf92b4a1daeb1b2ef04c5c604a24aac5bae93ccd`.
Snapshot: `/private/tmp/pcc-native-fragment-v78`.
Readiness: `build/native-fragment-v78-readiness.json`.
All editors are source-stable. Stage1 runs through the repository shared-lock
process-tree sampler with unchanged8GiB cap,360/410/440s stage/guard/outer
timeouts, GC0/threads-off, frontend7/backend2/link8 and immutable v76 runtime.
Expected wall160–220s is based on v76=160.98s and v77=186.45s, not a speed claim.

After Stage1, require actual pcc1 canaries and exact same-input PCO measurements
against both v77 and v76 before Stage2. The latest complete Stage2 remains
v76=484.762s. Full helper lists/placeholders, producer/instruction text, normal
ASM publication, verifier/CFG/def-use and Stage2<=Stage1 remain open.
