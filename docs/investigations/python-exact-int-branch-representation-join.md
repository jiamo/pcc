# Investigation: exact-int branch rebind loses the scalar fallthrough value

## Status

active

## Problem Description

A local annotated as Python `int` can use two different native
representations: a raw `i64` while its value is proven in range, or a boxed
exact-int object when arbitrary precision is required.  If only one branch
rebinds an existing scalar local to a bignum, current codegen replaces the
local with a pointer slot whose non-taken edge is `NULL`.  Reading the local on
that edge silently loses the earlier scalar value.

The immediate production trigger was `PyObject_VectorcallMethod`: a branch
assigned `-0x8000000000000000`, which was incorrectly classified from its
positive operand as a bignum.  Folding the signed literal before range
classification fixes that trigger, but a real bignum such as `1 << 70` still
demonstrates the broader representation-join defect.  Python arbitrary-
precision semantics rule out fixing this by wrapping or force-unboxing the
bignum.

## Repro

The permanent minimized reproducer is:

```bash
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_typed_int_unboxed.py::test_c_abi_i64_branch_boxes_fallthrough_when_other_branch_needs_bignum
```

Current result is one strict xfail.  Its emitted function contains a pointer
phi with a `NULL` fallthrough instead of a boxed representation of the initial
`7`.

## Test [CONFIRMED]

The checked strict-xfail test above reproduced the defect on 2026-08-08 in
0.35 seconds.  The neighboring non-xfail regression proves that folded
`-2**63` stays in the scalar `i64` lane and prevents the production vectorcall
trigger from returning.

Closure of this investigation additionally requires branch-order, if-
fallthrough, while-zero-iteration, bignum comparison, ownership/rooting, and
pcc1 bootstrap coverage.

## Proposals

- No.1 Function-level exact-int representation preanalysis [pending]
- No.2 Repair a missing pointer value at each read [DENIED]
- No.3 Force every typed `int` into raw `i64` [DENIED]

## No.1 Function-level exact-int representation preanalysis

### Code Change

Before emitting a function, compute the local names that can receive an
exact-object integer on any control-flow edge.  Allocate those locals as
boxed/rooted pointer slots from function entry and box every scalar assignment
to them, including parameters and initializers.  The current dynamic
scalar-slot-to-pointer replacement then becomes unreachable for those names.

The analysis must cover if fallthrough and zero-iteration loops without
reviving the historical child-scope propagation change that broke the
pcc1-to-pcc2-to-pcc3 fixed point.

### pending

No implementation has been attempted.  This is the bounded follow-up task
`PY-P0-EXACT-INT-BRANCH-REPRESENTATION`.

## No.2 Repair a missing pointer value at each read

### Code Change

Treat `NULL` in the widened pointer slot as a request to recover or rebox the
old scalar slot.

### DENIED

The old scalar value has no reliable control-flow or ownership identity after
the slot replacement.  A read-time fallback would spread representation and
GC-root policy across every consumer and can confuse a legitimate null-like
failure with an uninitialized edge.

## No.3 Force every typed `int` into raw `i64`

### Code Change

Unbox bignum assignments into the existing scalar slot.

### DENIED

This would make Python `int` wrap, truncate, or fail where Python requires
arbitrary precision.  Raw fixed-width behavior belongs only to explicit
`pcc.i64`/`pcc.u64` projections or a proven-in-range optimization.

## Update — 2026-08-12 source implementation

The current working tree contains the bounded No.1 implementation and focused
regression source.  Function emission now inventories representation writes
before creating the entry block, promotes forced parameters to owned rooted
objects, allocates one pointer slot for every forced local, and uses that slot
across Assign, AugAssign, comparison, return, loop rebind, normal cleanup and
the shared error epilogue.  The adjacent generic for-target join uses the same
owned/updateable-root invariant rather than storing an object pointer into an
older scalar alloca.

This update is deliberately not a CONFIRMED verdict: the user requested an
implementation-only phase, so none of the focused IR/runtime, bootstrap, or
sequential pcc1 -> pcc2 -> pcc3 gates have run against this source yet.  The
investigation remains active until those observations are recorded.

## Update — 2026-08-14 Harness repeated-dict target

The PCC-native Harness credentials provider exposed an adjacent generic join:
one function iterates a value returned by `dict.copy()` and then its typed
source dictionary using the same target name. The first loop currently records
the target as CPython-backed while the second selects the native object lane;
`_for_prepare_owned_object_target` rejects the join under
`--python-libpython=off`. The permanent minimized regression is
`test_repeated_dict_iteration_reuses_one_native_object_target`; its focused
failure must be observed before extending No.1 to normalize both loop writes
to the function-planned object slot.

The minimized failure showed that the first loop was not an additional
for-target join case: typed `dict.copy()` had lost the receiver's `DictType`
and fallen through to a CPython-backed method call. Container method inference
now preserves the receiver type for `list.copy()`, `dict.copy()`, and
`set.copy()`, and typed/dynamic dictionary lowering dispatches zero-argument
`copy()` to the existing native `py_copy_copy` runtime entry point. The
focused regression passes and its IR contains two native `py_dict_keys` calls
sharing one owned object target slot:

```bash
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_py_for_target_representation_join.py::test_repeated_dict_iteration_reuses_one_native_object_target
```

Result: `1 passed in 0.31s`. The full file remains blocked at the earlier
CPython-to-native target migration case tracked by the main investigation, so
this adjacent Harness trigger is fixed without changing the investigation's
active status.

## Update — 2026-08-31 nullable-int dynamic bitwise projection

The worker-owned direct-`.pco` Stage1 v32 canary exposed a distinct exact-int
consumer bug after the earlier representation fixes.  The pcc1 assembler
parsed four non-zero stable function IDs but encoded every numeric `.quad` as
zero.  Decoding the retained object proved that its four stack-map function
IDs were all zero; the host assembler encoded the same 3,576-byte stack-map
payload with the expected non-zero IDs.

The minimized pcc1 program mirrors `arm64_asm_driver._parse_int`:

```python
def parse_int(token: str) -> int | None:
    return int(token, 0)

value = parse_int("2891786578161389964")
if value is not None:
    encoded = (value & ((1 << 64) - 1)).to_bytes(8, "little")
    print(int.from_bytes(encoded, "little"))
```

With direct `.pco` publication disabled only to isolate expression semantics,
the frozen v32 pcc1 printed `0` instead of `2891786578161389964` under
`tests/python/test_pcc1_python_smoke.py::test_pcc1_bigint_mask_to_bytes_preserves_quad_value`.
The same expression is correct when `value` is an ordinary inferred `int`, so
the failure specifically requires the nullable return's boxed `DynType`
projection.

Host-vs-pcc1 IR comparison established the mechanism: the Dyn bitwise path
calls `py_int_to_i64` for both operands without consuming its overflow flag.
The `(1 << 64) - 1` operand therefore becomes zero before the native `and`.
This is not No.2's denied read-time recovery and must not be repaired by
No.3's denied force-i64 projection.

Proposal No.4 is to give Dyn `&`/`|`/`^` the same tagged fast path plus generic
boxed slow path already used for Dyn `+`/`-`/`*`.  The slow path must preserve
arbitrary-precision ints and dispatch supported set/dict, extension-number,
and user `__op__`/`__rop__` semantics; it may not assume every Dyn value is an
int.  Status: pending implementation and focused C/pcc-Python differential,
IR-shape, pcc1 runtime, direct-`.pco`, and Stage1-canary gates.

## Update — 2026-08-31 v33 isolates the preceding dynamic shift

Proposal No.4 is source-complete and its focused boundary is confirmed: the
Dyn bigint/set/dict runtime+IR test passes 3/3; user/reflected dunder dispatch
passes under both the pcc-Python and C runtime mirrors; the pcc-Python module
strict closure check passes; and the 225-module contextual compile remains
zero-fallback with the assembler IR calling `py_obj_and`.  A v33 pcc1 also
passes the minimized nullable-int bitwise test when its width is a statically
known `int`.

The v33 Stage1 strong canary nevertheless still encoded all four stack-map
function IDs as zero.  A breakpoint at the exact `assemble_file` call site
proved `py_obj_and` received a valid heap-int left operand but the tagged-int
zero (`0x1`) as its right operand.  Therefore No.4 was not the remaining
fault: its input mask had already become zero.

The additional minimized pcc1 repro is `width = opaque(8)` followed by
`(1 << (width * 8)) - 1`; it prints zero under v33.  The Dyn shift path still
projects both operands to i64 and emits a machine shift.  AArch64 masks a
64-bit shift count to zero, so this becomes `(1 << 0) - 1 == 0`.  Proposal
No.5 is the analogous generic boxed dispatch for Dyn `<<`/`>>`: exact ints use
`py_int_shl`/`py_int_shr`, extension numbers use their number slots, user
objects use `__lshift__`/`__rlshift__` and `__rshift__`/`__rrshift__`, and
negative counts retain Python exceptions.  Explicit `pcc.i64`/`pcc.u64`
machine-lane shifts keep their existing modulo-width contract.
