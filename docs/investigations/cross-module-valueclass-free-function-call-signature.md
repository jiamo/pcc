# Investigation: free-function call signatures retain valueclass shells

## Status

active

## Problem Description

The native fragment vertical passes host ASM/PCO differentials but its full
228-module compiler context rejects two cross-module free-function calls.
Physical parameters and source values are correct {i64,i64} aggregates, while
the call-signature annotation used for marshalling is an empty ordinary class.

This is beyond the resolved valueclass method/return export investigations,
which were read end to end. Their class/method expansion is not a fix for the
unexpanded top-level function call_sig path.

## Repro

`test_direct_publication_uses_exact_static_abi_in_stage1_context` fails in
50.51s with `marshal_to_object: unexpected IR type { i64, i64 } for class object`.
Only self_backend_aarch64_darwin_slots and self_backend_precise_stackmaps fail.
The complete traceback is `build/native-fragment-full-context.log`.

## Test [CONFIRMED]

A three-module records/provider/consumer reduction reproduces the failure
without a compiler rebuild. In-process marshal instrumentation also names the
two real boundaries:

- slots.append_slot_base_address_parts -> regs.append_add_offset;
- FunctionStackMapPlan._append_reload_span_packed -> slots.append_load_slot_to_reg_parts.

Both have physical param {i64,i64} and a source CompilerInt2 with valueclass=True
and two fields. The call_sig-derived target has module='', fields=() and
valueclass=False. The provider's param_types is already expanded correctly;
its call_sig annotation remains ('class','CompilerInt2','',(),()).
Exact source/metadata/stack/partial IR receipts are under
`build/native-fragment-aggregate-abi-attribution/`, including
`real-marshal-failures.json`, `real-counts.json` and `real-targets.log`.

## Proposals

- No.1 Expand top-level function call_sig annotations alongside param_types [pending].
- No.2 Box handles, split their fields at every call, or duplicate records [DENIED].

## No.1 Complete function signature expansion

### Code Change

`_expand_local_valueclass_export_refs` currently expands return_ty/param_types,
then skips non-class entries. Only the later method loop expands call_sig.
Apply the same descriptor expansion to function call signatures before that
early continue, preserving argument names/kinds/defaults and the existing
special treatment of method receivers. Regress imported/re-exported valueclass
parameters in positional and keyword calls, requiring direct aggregate ABI and
no object-boxing adapter. Cover idempotence and native-export wire roundtrip.

### Pending

Run the minimal red/green regression, replay both real context failures, inspect
new fragment/helper bodies for stubs or dynamic aggregate projection, and then
execute a pcc1/native canary before any expensive source-frozen stage.

## No.2 Representation workaround

### DENIED

The physical ABI and class definition are already correct. Object boxing,
per-field scalar adapters or duplicate record classes would conceal a generic
export defect and repeat the predecessors' denied interface workarounds.
