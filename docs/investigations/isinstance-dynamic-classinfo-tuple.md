# Investigation: dynamic isinstance classinfo tuples return false

## Status
active

## Problem Description
The external gateway compiles under host pcc, but pcc1 rejects a string join
as an unresolved parking method. Its effect analyzer passes a module-level
tuple of builtin receiver types to isinstance. A reduced native program
prints True for the literal class and False for a global tuple containing
the same class. CPython prints True for both.

## Repro
Compile a dataclass Child and `TYPES = (Child, int)`, then evaluate
`isinstance(Child("str"), TYPES)`. The v4 Stage1 candidate at
`build/gateway-stage1-20260906-v4/pcc1` returns False.

## Test [CONFIRMED]
The standalone `/tmp/pcc_type_tuple_probe.py` compiled by v4 printed
`True`, `False`, `StrType`. The retained gate is
`tests/python/test_isinstance_dynamic_tuple.py`, including nested tuples,
builtin bool/int inheritance, a nonmatch and an empty tuple.

## Proposals
- No.1 recursively examine runtime tuple classinfo [pending]

## No.1 recursively examine runtime tuple classinfo
### Code Change
Both runtime mirrors now recursively inspect tuple classinfo before rejecting
non-class values. py_tuple_get returns an owned reference, released after each
recursive check; the tuple retains its own element reference throughout.
The frontend already routes dynamic classinfo through this helper; literal
tuple checks take a separate path. Invalid non-class classinfo retains the
preexisting behavior; this change does not claim full classinfo validation.

### Validation
Both C and pcc-Python runtime tests passed in 132.68 seconds. The subsequent
combined membership/classinfo suite passed four cases in 139.60 seconds.
The rebuilt native compiler's string effect guard remains to be qualified.
