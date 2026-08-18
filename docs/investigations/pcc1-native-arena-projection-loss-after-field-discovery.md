# Investigation: native arena projection lost after field discovery expansion

## Status

active

## Problem Description

v75 fixes generic method aggregate comparison and nested class-field exports,
but its frozen GC0 Stage2 exceeds the established 8GiB process-tree cap at
310.87s. Earlier canaries and host context checks were green. The failed phase
is indexed ASM emission; no pcc2 or Stage3 result exists.

## Repro

Same retained v58 sidecars with v74 and v75 compilers under the shared sampler:

- CLI module_1: v74 finishes in about29s; v75 times out60s on the identical
  input, with sampled peak5.425GB. No timeout residue remains.
- unsafe_lowering module_206: both finish on the identical input. v74 CPU
  12.30s, 180.776B instructions, process max1.625GB; v75 CPU36.95s,
  541.569B instructions, process max2.987GB. ASM bytes are identical,
  SHA256 `701b576319edb93bd83cb1819eb00c3c34495f8a09286d6a08df5a3ba92375dd`.

Receipts: `build/span-foundation-v75-oldcli-{control,candidate}/` and
`build/span-foundation-v75-unsafe-{control,candidate}/`.

## Test [CONFIRMED]

The same-input native compiler regression is confirmed, independently of input
growth or scheduling. v75's current CLI PIDX has the same60659064-byte size
as v58 but a different hash; therefore current-vs-old input was not treated as
an identical-input comparison. The timed A/B above uses the exact old file in
both arms. Scheduler sources, lane counts and admission floors are unchanged.

## Proposals

- No.1 Preserve declared fields when constructor RHS inference is unknown [CONFIRMED at native worker boundary; full stages pending].
- No.2 Raise resource limits or repeat full Stage2 [DENIED].

## No.1 Preserve native field projection

### Evidence and Code Change

The new `_class_fields_from_def` fallback assigned DynType for unknown RHS and
then `_append_field` overwrote precise class declarations. Conditional/adopted
arena initialization is intentionally outside `_init_field_rhs_type` inference.
`IndexedFunctionKernel.block_phi_facts: CompilerIntArena`, for example, became
dynamic after its `CompilerIntArena() if seed is None else seed.block_phi_facts`
assignment.

Contextual IR comparison via the existing `pcc.ir_diff.IrSummary` parser shows
243→91 direct arena call sites, 4→132 `py_obj_call`, 46→174 `py_tuple_new`,
and 0→68 `py_valuebox_get_field` in non-adapter kernel methods. Fifty-nine
methods gain dynamic calls; eighteen small aggregate getters lose direct
aggregate dispatch. These IRs locate the source mechanism; native A/B above
establishes the execution regression.

Correction: unknown RHS preserves an already established type;
only genuinely missing fields receive DynType. Regress conditional and adopted
attribute initialization and assert direct native getter IR before rebuilding.
The field-order, constructor-precedence and dataclass fixes must remain.

The dynamic boxed-result path also appears to omit a result release. That is
a separate ownership candidate requiring validation; restoring precise fields
must not be claimed to repair general dynamic return ownership.

### Contextual restoration

Conditional/adopted-attribute schema and direct-getter IR regressions were red
before correction. The focused correction passes21 tests. The full context
passes in46.74s, and comparison restores direct arena calls to243, dynamic
calls to4, tuple allocations to46 and ValueBox field reads to0. All18 aggregate
getters recover their v74 instruction counts and native aggregate dispatch.
The full contextual test now ratchets those18 method bodies explicitly.

The remaining69 static release sites and18 pin/unpin pairs accompany18 newly
typed list accesses across14 functions. They release the owned list field
reference returned by `py_instance_get_field`; those static counts are not
runtime measurements. Native v76 restoration and Stage2 retry remain pending.
The separate dynamic ValueBox ownership issue is source-validated and tracked
as `PY-P1-DYNAMIC-VALUECLASS-RESULT-OWNERSHIP`.

## No.2 Raise limits or retry the full stage

### DENIED

The native A/B is roughly3x instruction/CPU work and substantially higher memory
for identical output. A larger limit would conceal an implementation regression.
No new full Stage2 is allowed until the focused correction and native boundary
show restoration. Stage2<=Stage1 remains the performance contract.

## Update: v76 native restoration

The corrected frozen compiler builds successfully in 160.98s and passes all
four native canaries. Fresh identical-input v74/v76 controls produce exact
assembly: unsafe CPU 12.46→11.48s with unchanged 1.625GB process max RSS; CLI
CPU 29.44→27.14s with unchanged 4.477GB process max RSS. The original v75
Stage2 CLI input also completes in 27.10s wall / 27.04s CPU and 4.477GB.
Receipts are named and source-bound in evidence065. The regression is removed
at the real native worker boundary without reverting the correctness fixes.
A single frozen GC0 Stage2 retry is now justified under the original 8GiB cap.
Full Stage2 and fixed point remain unproven.

## Update: complete GC0 stages pass

The unchanged-cap Stage2 retry succeeds in 484.762s and its pcc2 passes the
generic ABI executable canary. Stage3 succeeds in 554.124s; pcc2/pcc3 are raw
byte-identical. [Evidence066](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/066-span-foundation-frozen-stages.md)
contains source/binary identities and terminal receipt readback. This confirms
the correction at the original complete-stage boundary; it does not waive the
remaining fallback checks or the Stage2<=Stage1 performance contract.
