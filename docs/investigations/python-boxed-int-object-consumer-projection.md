# Investigation: object consumers narrow the result of int(value)

## Status

active

## Problem Description

Issue #194 reduces the array numeric work in #191 to a generic Python
projection defect. An arbitrary-precision integer passed as `object` survives
until `converted: object = int(value)` chooses the ordinary scalar builtin
lowering. It unboxes through `py_int_to_i64` and boxes the narrowed value again.
The object-producing builtin already exists; the annotated object assignment
does not ask for it. This is separate from #178's repaired integer literals and
from #193's comparison semantics.

The predecessors are
[typed integer projection](typed-int-unboxed-overflow-silent-wraparound.md) and
[Stage2 projection experiments](pcc1-stage2-emit-throughput-and-memory.md),
read end to end before this proposal. The latter's No.23 leak attribution was
retracted by No.24's matching bignum control. Its No.25 still rules out creating
object-phi blocks inside a speculative producer. The measured warning beside
`marshal.py`'s unbox/recover shortcut remains relevant: retaining a recovered
pointer leaked, while borrowing could free it too soon. This proposal selects
the existing producer before information loss and uses its ownership ledger.

## Repro

The regression in `tests/python/test_int_builtin_object_projection.py` passes
`9223372036854775808` through an annotated object local and prints both values.
CPython prints the integer twice; current host pcc, self backend, no-libpython,
private C runtime prints the integer and then `0`.

Run the focused test with `-x -n0` through `run_process_tree_sample.py`, setting
`PCC_RUNTIME_HIGH=c`, `PCC_RUNTIME_CC=cc`, and `PCC_RUNTIME_ARCHIVE` to the
immutable private archive. The retained command and source environment are in
`build/correctness-20260906-a/int-object-projection-red-01.result.json`.

## Test [CONFIRMED]

On 2026-09-07 the minimal native test failed in 3.84 seconds. Artifacts are
`build/correctness-20260906-a/int-object-projection-red-01.{stdout,stderr,result.json,pytest.jsonl}`.
Temporary emitter instrumentation recorded `IntType` for the call result and
`DynType` for its argument when the ordinary builtin was selected. Thus this is
a destination-projection defect, not an incorrectly inferred call type.
This current-source C-runtime observation is distinct from B's earlier frozen
pcc1 artifacts and does not qualify pcc1 or a bootstrap fixed point.

## Proposals

- No.1 Route object consumers to the existing exact object producer [pending]

## No.1 Route object consumers to the existing exact object producer

### Code Change

Object locals and object returns of semantic integer expressions should use
the same object projection already used by container elements. Preserve the
producer's new-reference ledger and ordinary builtin binding. Verify direct
returns, containers, negative/full-width integers, string and small controls;
inspect typed `__int__` separately if its supported route loses the result.
Do not change C conversions or recover a pointer after narrowing.

### Pending

The minimal red is established. Focused green, ownership/dispatch controls,
and fresh pcc1/bootstrap qualification remain pending.

## Update 2026-09-07: partial consumer milestone before requested checkpoint

The first patch selects `_emit_expr_as_pcc_object` for semantic integer values
assigned to object locals or returned as object. The string producer records
its new result in the existing ownership ledger. The minimal native regression
then passed in 6.05 seconds (`int-object-projection-green-01` artifacts).

The expanded consumer packet stopped at its first failure: **1 passed, 1 failed
in 14.41 seconds**, in `int-object-consumers-01` artifacts. Object locals,
direct object returns, reassignment, and the homogeneous dictionary case all
preserved signed and full-width integers. Mixed list and tuple literals still
printed `0` for `int(value)` above the scalar lane. Their staged operand paths
in `literal_lowering.py` call `_emit_expr_with_cpy_operand_cleanup` without its
object-projection option, unlike the homogeneous fast paths. The mixed-dict
staging family must be audited with those paths before the next native retry.

The test file also contains an IR-owner ratchet and typed `__int__` large-result
control, neither reached by the stopped packet. The existing class conversion
source unboxes the object returned by `__int__`; its native outcome remains
unmeasured here. Builtin alias/shadow binding and borrowed integer identity
ownership controls remain pending. No pending control is green evidence.

The user requested a temporary all-work commit/push checkpoint at this point.
Implementation and tests are intentionally held at this partial state for that
checkpoint. #194 stays open; no current pcc1 or bootstrap claim follows from
the host-compiler/private-C-runtime milestone. Float-to-int range and NaN/inf
conversion are a separate capability boundary, not repaired by this routing
patch or the array helper's parse/wrap separation.
