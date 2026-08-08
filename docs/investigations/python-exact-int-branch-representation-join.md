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
