# Investigation: method ABI rejects distinct equal literal aggregates

## Status

active

## Problem Description

The native record-span experiment passes a `CompilerInt2` value into an
ordinary class method. Both the argument and parameter are `{ i64, i64 }`, but
method lowering rejects them as incompatible before self-backend emission.
This is separate from the resolved cross-module valueclass return-export and
aggregate-signature parser defects (both predecessor investigations were read
before editing).

## Repro

`tests/python/test_compiler_record_spans.py::test_record_span_native_self_backend_executes_aggregate_handles`
compiles the actual value-arena module, unsafe module and a tiny caller. The
frontend worker stderr names both `CompilerRecordSpanArena.append` and `_root`:

```text
argument 0 lowered as { i64, i64 }, but the emitted method ABI requires { i64, i64 }
```

## Test [CONFIRMED]

The native compile test failed in 1.37s before executing an output. A smaller
comparison test constructs two separate `LiteralStructType([i64, i64])` values
and directly calls `_method_abi_type_matches`; it fails before the fix.
Negative cases cover field width/order, array length, packing and address space.

## Proposals

- No.1 Compare literal aggregates structurally at the method ABI boundary [pending].

## No.1 Compare literal aggregates structurally

### Code Change

Recurse through anonymous struct elements and array element/count
pairs in `_method_abi_type_matches`. Preserve its opaque-pointer address-space
rule and leave the general load/store pointee matcher unchanged. Do not equate
identified structs by body or bypass semantic argument marshalling.

### Evidence

The current helper delegates to `CoreHelperMixin._ir_type_matches`, which only
handles identity, integer widths and pointees. Neither width nor pointee exists
on a literal structure, so two equal independently constructed aggregate types
return false. No textual IR rewrite or compiler-record special case is needed.

### Focused result

The comparison/negative tests pass. The actual span program then compiles and
executes its aggregate-handle calls and returns the correct sum/count. That
exposed the independent nested-field export defect; after its separate repair
the entire unchanged native output assertion passes. The existing ABI/field/
span packet is 21 passed in 20.72s. Contextual, pcc1 and fixed-point evidence
remain open; no full bootstrap claim is made.

## Update: native and fixed-point qualification

The v76 contextual and source-checked pcc1 tests pass. Receipt-selected pcc2
also compiles aggregate arguments, nested fields and inherited dataclasses to
a successful executable. Frozen GC0 Stage2 and Stage3 pass; pcc2/pcc3 are raw
byte-identical. See [evidence066](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/066-span-foundation-frozen-stages.md)
for identities, exact receipts and timings. No.1 is CONFIRMED at the full
fixed-point boundary. The task remains active until fallback baseline shards
complete; the unrelated dynamic ValueBox ownership issue is not claimed fixed.
