# Investigation: shared field walker lacks a standalone static export

## Status

active

## Problem Description

The new shared instance-field assignment walk repairs native field ordering,
but its import is not registered in the standalone static export table.
OFF fallback qualification reports class_gen 25 actions against 14 and
type_infer 10 against zero. The frozen native GC0 fixed point remains green;
these are separate, mode-labelled execution boundaries.

## Repro

`test_per_module_fallbacks_under_ratchet` fails in the isolated OFF standalone
shard: 1 failed, 1 passed in 61.42s. Full traceback is retained in
`build/span-projection-restored-v76-fallback-off-standalone.log`.
Fresh standalone IR and source-hashed receipts are under
`build/field-helper-off-attribution/`.

## Test [CONFIRMED]

All 11 class_gen additions occur in `_collect_method_instance_fields`; all
10 type_infer actions occur in `_class_fields_from_def`. Both import
pipeline_exports, fetch instance_field_assignment_statements, call it through
py_cpy_call1, iterate with CPython and read statement fields through CPython.
An in-process diagnostic registration using the existing truthful
Dyn -> List[Dyn] function schema restores the exact 14/0 counts without source
edits. Readback: `static-helper-injection.json` and the two `_static_helper.ll`
files in that artifact directory.

## Proposals

- No.1 Register the shared walker in the existing static export mechanism [pending].
- No.2 Increase the two consumer ceilings [DENIED].

## No.1 Static function export

### Code Change

Add the generic provider function's real native call/return contract using the
existing static export registry. Add a standalone consumer regression which
imports the walker, iterates the result and reads a statement field. Require
the native call and zero CPython actions. Preserve its full contextual ABI.

### Pending

Run the focused regression and both real consumer probes, then contextual and
native qualification appropriate to the registry change. Other pipeline/native
module ratchet failures have separate attribution and are not covered here.

## No.2 Higher consumer ceilings

### DENIED

A missing registration with a successful causal substitution is a repairable
resolution regression. Naming the underlying field-order feature does not
justify keeping this avoidable CPython import/call path.

## Update: focused repair and complete fallback gates

The registry now supplies the proven native Dyn -> List[Dyn] function schema.
The standalone import/iteration/statement-field regression was observed red,
then all four OFF/ON consumer cases passed. Actual class_gen/type_infer output
recovers 14/0 actions in both modes. Known, unlisted and missing provider-attr
controls show direct native dispatch, real compiled-module lookup and explicit
AttributeError respectively, with no strict unavailable stub.

Every fallback and IR-baseline assertion now passes in bounded shards, with
37/37 fallback node coverage and 8 IR nodes. Receipt:
`docs/goal/evidence/HARNESS-P1-FALLBACK-PHASE-SHARDS/001-phase-isolation-and-current-fallback-surfaces.md`.
The static table is a production-source edit after v76; a fresh relevant
native compiler/fixed-point qualification remains before this row closes.
