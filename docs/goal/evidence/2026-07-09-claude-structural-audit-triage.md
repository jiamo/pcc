# Claude structural audit triage

Date: 2026-07-09

Source: Claude session-level code-smell audit, now internalized here so the
task board does not depend on a transient `/private/tmp` scratchpad path.

The audit contains three classes of findings:

1. Low-risk, directly verifiable bugs. These were either already fixed in the
   current staged patch or covered by existing task rows:
   - implicit C float-to-unsigned conversion using `fptosi` instead of
     `fptoui`;
   - package install/native-support claim conflation;
   - pcc1 compatibility-runner requested mode being reported as actual mode;
   - empty native linkage scans claiming native package support.

2. Structural correctness risks that deserve task-board rows, not opportunistic
   drive-by edits:
   - backend-4 relocation payload copy is a second object-graph rule beside the
     slot visitor/update-slot contract;
   - runtime C and pcc-Python mirrors have independent data structures or
     magic constants for GC indexing, class layout, finalizer cache behavior,
     set/dict perturbation, and backend0 frame-root selection history;
   - package and pcc1 manifests have multiple schema/wheel-tag/mode-label
     sources of truth;
   - the pcc1/bootstrap/host CLI surfaces have multiple independent flag and
     execution-path definitions;
   - C IR text postprocessing grew beyond the va_arg-only exception;
   - package campaign code contains a package-name-specific `numpy-core-l6`
     branch;
   - Python frontend lowering and C codegen keep multiple parallel lowering or
     metadata propagation paths.

3. Maintainability debt that should be tracked but is lower priority unless it
   blocks a correctness gate:
   - god methods/classes;
   - dead or test-only roadmap modules;
   - duplicated registries and helper implementations;
   - weak claim or oracle quality in selected tests.

Task-board absorption status after this triage:

- Existing rows already cover GC slot visitor, GC barrier audit, runtime
  tripwires, hoist/layer1 split, subprocess timeouts, xfail/oracle claim audit,
  native-extension ladder, and builtin exception tag dedup.
- The remaining meaningful structural findings listed above are split into
  task-board rows. The 2026-07-09 task/gate split evidence lives in
  `docs/goal/evidence/2026-07-09-structural-migration-task-split.md`.
  They are intentionally marked `TODO_NEEDS_DESIGN` or `CLAIM_RISK` unless a
  focused gate already proves a safe path.
