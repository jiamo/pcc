# Investigation: IR class set in the integer global bridge fails under pcc1

## Status
active

## Problem Description
The gateway facade re-exports PCC_TLS_REQUIRED_CAPABILITIES, computed by
bitwise OR in a raw-integer module. Host pcc compiles it; pcc1 raises
`NameError: name 'ir' is not defined` during declarations.

## Repro
Provider: `from pcc.unsafe import null; FIRST = 1; SECOND = 2;
LIMIT = FIRST | SECOND`. A facade imports LIMIT, and main imports that
facade and prints LIMIT. Native v4 fails for both main and facade; expected
output is 3. The retained test is
`tests/python/test_cross_module_int_global_reexport.py`.

## Test [CONFIRMED]
The reduced program failed under `build/gateway-stage1-20260906-v4/pcc1`.
Replaying the original deferred gateway worker under LLDB stopped at
py_exc_new(10), called from _declare_native_module_extern_global + 8796.
The host worker reported provider `box_int_abi=False`, consumer `BOX True`,
and local slot `ptr` for PCC_TLS_REQUIRED_CAPABILITIES.

## Proposals
- No.1 use supported IR type predicates [pending native qualification]

## No.1 use supported IR type predicates
### Code Change
Replace a set of first-class scaffold classes `{ir.IntType, ir.PointerType}`
with two literal-tuple isinstance predicates, which the scaffold lowers
directly. The check still rejects other storage types; the existing integer
representation bridge and runtime integer semantics are unchanged.

### Validation
Pending a new native compiler and original gateway worker execution.
