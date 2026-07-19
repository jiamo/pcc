# Structural migration task split

Date: 2026-07-09

Scope: split the remaining Claude audit items that are too large for drive-by
fixes into independent task-board rows with gates. This file is not completion
evidence for those migrations; it records the packaging of the work so agents
do not mix unrelated structural changes.

Task rows:

- `AUD-P1-C-IR-POSTPROCESS-POLICY` covers only the AArch64 branch-protection
  rewrite inside `postprocess_ir_text()`. The gate requires a new source guard
  that leaves va_arg as the only text rewrite exception and a behavior gate
  proving pac-ret/BTI policy still exists outside text surgery.
- `AUD-P1-PACKAGE-NO-SPECIAL-CASE-CAMPAIGNS` covers only the `numpy-core-l6`
  package-name campaign branch in host and pcc1 package campaign surfaces. The
  gate requires a generic capability profile/fixture while preserving the
  current report shape.
- `AUD-P1-GC-INDEX-TABLE-SOURCE-OF-TRUTH` covers only the C versus pcc-Python
  object-to-node index-table duplication. The gate requires one spec/generated
  contract or a parity guard for insert/find/delete/resize/collision/tombstone
  behavior, plus focused GC and five-backend bootstrap checks.
- `AUD-P0-GC-RELOCATION-SLOT-CONTRACT` covers only backend-4 relocation's
  second slot-layout rule. The gate requires `pcc_gc_relocate_copy_payload` and
  the pcc-Python `_relocate_copy_payload` mirror to consume the shared slot
  visitor/update-slot contract or a generated equivalent.
- `AUD-P1-CLI-ENTRYPOINT-SOURCE-OF-TRUTH` covers only the three CLI entrypoint
  drift problem across `cli_core`, `cli_bootstrap`, and `pcc.py`. The gate
  requires one declarative shared-flag owner or explicit divergence records,
  plus host CLI tests and a pcc1 build.

Non-goals for this split:

- Do not implement pcc-native NumPy as part of campaign cleanup.
- Do not combine GC index-table work with backend-4 relocation slot semantics.
- Do not use the postprocess cleanup as a broad LLVM-CAPI or sanitizer refactor.
- Do not remove click or rewrite the bootstrap CLI under the CLI source-of-truth
  task unless a later task explicitly asks for that migration.
