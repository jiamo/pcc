# M3 typed-array projection finite contract

Task: `M3-TYPED-ARRAY-PROJECTION`

## Why this is a new production slice

`pcc.value_model.SpecializedArray` is host-only planning metadata.  Ordinary
`list[Point]` stores boxed objects and therefore cannot be renamed a dense
typed array.  This task introduces one compiler-owned fixed-length array shape
without claiming the full V4 runtime container from
`python-valhalla-value-model-plan.md`.

## Selected source surface

The finite MVP is:

```python
@pcc.valueclass
class Point:
    x: float
    y: float

def kernel(values: pcc.array[Point, 2], index: int) -> Point:
    return values[index]

points = pcc.array[Point, 2](Point(1.0, 2.0), Point(3.0, 4.0))
```

The explicit element type and length make the aggregate ABI auditable and
avoid silently changing ordinary list semantics.  The initial production
surface is deliberately limited to lengths 1..7, matching the selected
self-backend literal-struct ABI already used by valueclasses.  Dynamic-length
construction, append, aliasing mutation, slices, NumPy compatibility, and a
heap-resident V4 container remain out of scope.

## One semantic type, three boundaries

The array element representation is exactly the existing `Point` value
payload.  For the two-element example the direct-call ABI is conceptually:

```text
Point payload       { double, double }
array payload       { { double, double }, { double, double } }
kernel argument     same array payload (no PyObject*/ValueBox element array)
```

- Typed construction/index/call stays in the aggregate value projection.
- A dynamic index is converted through Python's index projection and checked;
  an unrepresentable index raises `OverflowError`, and an in-range machine
  integer outside `[0, length)` raises `IndexError`.
- An element crossing to `Any` uses the existing ValueBox object projection.
  The array itself has no `Any` escape in this MVP; attempting one receives a
  stable unsupported-boundary diagnostic rather than a silent list rewrite.

## Backend ownership and parity

The frontend emits ordinary LLVM aggregate/extract/control-flow operations.
The LLVM builder and self backend both consume that pcc-owned IR; neither may
silently replace the array with a host Python list.  Parity means identical
observable results and the same nested aggregate signature, not byte-identical
machine code.

## Finite implementation slices

### TA-S1 — source/type contract

- Add `ValueArrayType(element, length)` to the typed AST.
- Recognize only `pcc.array[ValueClass, literal_length]` annotations/calls.
- Require a valueclass element, integer literal length 1..7, exact argument
  count, and matching element type.
- Add a small host-Python `pcc.array` oracle surface with the same construction,
  len/index, and error behavior.

Gate: source-shape/type-inference tests for accepted and fail-closed forms.

### TA-S2 — shared aggregate ABI

- Map `ValueArrayType` to a literal struct containing repeated existing
  valueclass payload types.
- Lower constructor arguments directly into that aggregate.
- Admit direct typed parameters/returns and constant indexing without
  `py_instance_new`, `py_valuebox_new`, or list construction in the typed body.

Gate: LLVM IR signature/body assertions plus self-backend result parity for a
two-element float `Point` kernel.

### TA-S3 — checked index and element escape

- Lower dynamic indexing with one checked selection over the finite aggregate.
- Preserve Python negative-index behavior.
- Route unrepresentable indices to `OverflowError` and other out-of-range
  indices to `IndexError`.
- Reuse `_emit_valueclass_payload_to_object` only when the selected element
  crosses to `Any`; typed paths remain aggregate-only.

Gate: CPython host-oracle vs LLVM/self result, negative index, huge index,
out-of-range index, and explicit element escape tests.

## DONE_STRONG boundary

All three slices and their focused gates are green; LLVM IR proves the selected
kernel has the nested aggregate ABI and no alternate element object
representation; self/no-libpython runtime matches the host oracle for result,
negative index, overflow, out-of-range, and element escape.

This does not claim the V4 heap container, dynamic length, append/mutation,
pointer-bearing element GC tracing, arbitrary array lengths/layouts, NumPy
ndarray compatibility, or GPU buffer ownership.

