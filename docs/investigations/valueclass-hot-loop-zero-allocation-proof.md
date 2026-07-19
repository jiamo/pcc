# Investigation: prove a valueclass hot loop without mistaking tagged-int calls for heap allocation

## Status

resolved 2026-07-16

## Problem Description

`M3-VALUECLASS-ZERO-ALLOC` needs one combined, commit-bound proof that a
valueclass hot loop performs no heap allocation, matches an ordinary-class
Python oracle, and boxes correctly at an explicit escape.  The first test
draft equated "zero heap allocation" with "no LLVM `call` instruction in the
hot function".  That rejected the existing semantics-preserving tagged-int
projection even when the executed fast path allocates nothing.

## Repro

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_hot_loop_zero_allocation_oracle_and_escape_semantics

1 failed in 0.37s
```

The first call in the minimized IR is `py_int_from_i64(0)`.  The bounded
range induction is already raw `i64`; before its target is Python-visible it
re-enters the tagged Python-int projection.  For 0..999 that helper returns an
immediate tagged word rather than a heap object.  Tagged arithmetic also keeps
the bignum slow path required by Python semantics.

## Test [CONFIRMED]

The focused test above failed deterministically on the over-strong `no call`
assertion and printed the complete hot-function IR.  It showed no
`py_instance_new` or `py_valuebox_new`; the observed calls are tagged-int
projection, arithmetic slow paths, error checks, and GC root operations.

## Proposals

- No.1 Require a call-free hot function [DENIED]
- No.2 Combine aggregate IR shape with differential runtime allocation counts [CONFIRMED]

## No.1 Require a call-free hot function

### Code Change

Assert that the selected hot function contains no LLVM `call` instruction.

### DENIED

This confuses calls with allocation and would reward deleting Python's bignum
slow path.  `py_int_from_i64` returns a tagged immediate for the bounded loop
values, while slow-path `py_int_*` calls preserve semantics if an assumption
fails.  A call-free assertion is neither the requested allocation metric nor
a legal reason to weaken Python `int`.

## No.2 Combine aggregate IR shape with differential runtime allocation counts

### Code Change

Scope the static IR assertion to the actual representation contract: the hot
body contains aggregate valueclass operations and no instance/ValueBox
constructor.  Compile otherwise identical `range(0)` and `range(1000)`
programs under strict self/no-libpython mode with JSON allocation logging and
require identical total `alloc_object` counts.  Require the instance-tag count
to equal the two explicit `Any` escapes, then compare the hot result with an
ordinary Python class oracle and check the escaped boxes' identity, weakref,
and dynamic-attribute behavior.

### CONFIRMED

The combined test generates LLVM IR for the 1000-iteration form, verifies that
the hot function uses the `{i64, i64}` aggregate payload and contains neither
`py_instance_new` nor `py_valuebox_new`, and verifies that only the separate
`escape(...) -> Any` function calls `py_valuebox_new`.

It then compiles otherwise identical `range(0)` and `range(1000)` programs
with the self backend, `--python-libpython=off`, and JSON allocation logging.
Both executions produced the same total `alloc_object` count and the same
allocation type-tag histogram.  Each contained exactly two instance-tag
allocations, matching the two explicit escapes rather than the loop count.

The 1000-iteration result matched an ordinary Python class oracle.  Escaped
boxes preserved alias identity, distinct-box identity, and dynamic attributes;
weakrefs retained the established valueclass `TypeError` policy.  The first
expectation that the escaped box would reject dynamic attributes was corrected
after the runtime showed that an explicit object projection owns a box
`__dict__`; the raw payload diagnostics remain separate and unchanged.

Focused result:

```text
1 passed in 0.86s
```

Broader gates:

```text
tests/python/test_py_value_class_unboxed.py
47 passed in 5.95s

selected raw-identity diagnostics + boxed identity + dynamic weakref policy
12 passed, 56 deselected in 8.07s
```

## Report

Proposal No.2 closes the finite task without changing compiler or runtime
semantics.  The proof combines static representation shape with an executed
differential allocation metric, avoiding Proposal No.1's illegal implication
that Python's bignum slow path should be removed.  The claim is scoped to this
bounded Darwin-arm64 self/no-libpython hot loop; it does not prove every
valueclass loop is allocation-free or that cold bootstrap/build allocation is
zero.
