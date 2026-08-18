# Investigation: cross-module valueclass methods lose aggregate ABI

## Status

resolved 2026-08-27

## Problem Description

The compiler-internal packed-record tracer exposed a generic value-model gap:
a valueclass method defined in one module returns an LLVM aggregate locally,
but a typed caller in another closed-world module declares/calls it as `ptr`
and reaches the ValueBox adapter. The resulting merged IR has incompatible
caller/callee ABIs.

This is not an arena special case. `pipeline_context` exports class fields and
methods but omits whether the class is a valueclass; `_class_type_from_export`
therefore reconstructs every imported class as identity-bearing. A method
return descriptor naming that class resolves to the wrong physical projection.

Predecessors:

- `valueclass-wide-payload-aggregate-abi.md` proves the same-module aggregate
  ABI and adapter boundary;
- `pcc1-unannotated-cross-module-return-abi.md` establishes that export,
  declaration, and definition ABIs must agree;
- `python-valhalla-value-model-actual-state.md` records the broader projection
  limits.

## Repro

`tests/python/test_cross_module_valueclass_abi.py` compiles two tiny modules:
provider `Source.read() -> Quad` and consumer `pick(Source) -> int`. The
provider definition must return `{ i64, i64, i64, i64 }`; the consumer must
call the exact aggregate signature with no dynamic call or ValueBox.

The real reproducer is the compiler's `CompilerIntArena.get4_unchecked()`
provider and precise-stackmap consumer. In the merged IR, the definition
returns the four-i64 aggregate while the consumer calls it as `ptr`.

## Test [CONFIRMED]

The current merged compiler IR contains both incompatible shapes. The focused
test is added before the export fix and must fail on the consumer call
assertion.

## Proposals

- No.1 Export and reconstruct the valueclass bit on closed-world class schemas
  [selected].
- No.2 Move every value record into its consumer module [DENIED].

## No.1 Export and reconstruct the valueclass bit on closed-world class schemas

### Code Change

Classify `@valueclass`/`@pcc.valueclass` alongside dataclass source-shape
export, publish one `valueclass` boolean in the class export, and pass it to
both placeholder and final `ClassType` reconstruction. Existing `field_types`
then give imported annotations the same aggregate layout as the provider.

### CONFIRMED

Require focused merged-IR agreement, no-libpython closure, same-module
valueclass regressions, and the compiler packed-record pair gate.

## No.2 Move every value record into its consumer module

### DENIED

This duplicates compiler records, hides the generic ABI defect, and directly
contradicts the value-model projection contract. The export boundary owns the
fix.

## Report

Closed-world class exports now carry the valueclass bit and annotated fields.
A second deterministic export pass expands local valueclass annotation shells
inside function/method/field/call-signature descriptors after all class schemas
are known. Imported `ClassType` reconstruction retains that projection, and
method-call inference reads the same export descriptor used by codegen.

The focused provider/consumer test proves aggregate definitions, aggregate
calls, aggregate field extraction, no dynamic call/ValueBox in the caller, and
successful self-backend verification. The real compiler value-arena/stackmap
pair also compiles and self-emits with direct aggregate calls.
