# Investigation: valueclass payload ABI falls back after four fields

## Status

resolved 2026-07-16

## Problem Description

`V-P1-VAL` VP-S2 requires the selected self-backend scalar/aggregate
valueclass ABI to stay allocation-free across direct typed calls and returns.
A deterministic five-scalar-field valueclass instead lowers its constructor,
return, and argument as `ptr` objects, so the fifth field silently crosses the
boundary from payload projection back to instance allocation.

The existing one- through four-field and non-recursive nested-payload tests are
green. This investigation is limited to the first unsupported width and does
not change recursive type-cycle rejection or object-boundary boxing semantics.

## Repro

```bash
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_wide_payload_uses_aggregate_abi_self_backend
```

Expected: `make_wide` returns `{ i64, i64, i64, i64, i64 }`, `total` accepts
that payload directly, neither body allocates an instance/valuebox, and the
self-backend executable prints `15`.

Observed 2026-07-16: the IR assertions fail because both functions use `ptr`
signatures and the main path calls `make_wide() -> ptr` then `total(ptr)`.

## Test [CONFIRMED]

The focused test above failed deterministically: `1 failed in 0.37s`.

## Proposals

- No.1 Extend the existing scaffold-supported literal-struct arities from four
  through seven in both payload type mirrors. [CONFIRMED partial]
- No.2 Route aggregate parameters and returns through the existing valueclass
  box/unbox helpers in native function adapters. [CONFIRMED]

## No.1 Extend scaffold-supported literal-struct arities

### Code Change

Add the missing five-, six-, and seven-element `ir.LiteralStructType` branches
to `type_abi_lowering.py` and its deliberately local `class_gen.py` mirror.
The self-host IR scaffold already exports matching
`LiteralStructType___init__5/6/7` helpers; no generic dynamic-list lowering or
runtime ABI change is proposed.

### CONFIRMED partial

The two narrow branches moved the minimized repro from a `ptr` signature to a
real `{ i64, i64, i64, i64, i64 }` payload. The next run no longer failed the
signature assertion; it stopped while publishing the function-value adapter,
where generic `marshal_to_object` rejected the aggregate ClassType. The same
adapter regression is currently visible for the previously green nested
aggregate-return test, so adapter boxing is a separate boundary rather than a
self-backend parser failure.

## No.2 Route aggregate parameters and returns through valueclass helpers

### Code Change

Before generic `marshal_from_object`, let `_emit_native_func_adapter` use
`_emit_object_to_valueclass_payload` for aggregate-typed parameters. Before
generic `marshal_to_object`, let aggregate returns use
`_emit_valueclass_payload_to_object`. Keep scalar and ordinary-class adapter
behavior unchanged.

### CONFIRMED

The first adapter patch exposed a control-flow ownership bug: the adapter's
builder was active, but `current_function` and `_current_entry_block` still
named `main`, so valueclass unbox blocks were appended to `main` and the
adapter branched to unknown labels. Saving, switching, and restoring those two
function-context fields keeps the existing helper's allocas, type-error edge,
and success edge inside the adapter.

The nested test's old `main_ir = ir_text[main_start:]` assertion also included
all later adapter definitions. It now isolates the `main` function body, where
no boxing is allowed, while correctly allowing the published function-value
adapter to box its aggregate return.

Focused gate:

```text
tests/python/test_py_value_class_unboxed.py::{
  test_valueclass_wide_payload_uses_aggregate_abi_self_backend,
  test_valueclass_nested_payload_uses_payload_abi_in_direct_calls
}
-> 2 passed in 0.94s
```

## Report

Both boundaries are closed. Proposal No.1 extends the deliberately finite
self-host scaffold surface from four through seven payload fields in both ABI
type mirrors. Proposal No.2 keeps native function-value adapters on the same
valueclass box/unbox projection as direct typed calls while constructing all
adapter control flow in the adapter itself.

Final focused evidence:

- five-, six-, and seven-field aggregate ABI tests: `3 passed in 1.17s`;
- recursive direct pointer-payload roots under GC0..4: `5 passed in 1.53s`;
- instance/valuebox trace, update, and promotion share the unified
  `py_obj_visit_slots` owner walker: `1 passed in 0.06s`.

The claim is limited to the selected one- through seven-field scaffold ABI.
Eight-or-more-field direct payloads remain outside this finite self-host
surface and continue to box; recursive type cycles remain rejected.
