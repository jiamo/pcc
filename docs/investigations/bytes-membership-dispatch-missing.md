# Investigation: generic bytes membership always reports no match

## Status
active

## Problem Description
The native gateway dashboard produced a correct HTTP/1.1 200 response and
JSON body, but its `b"HTTP/1.1 200 OK" not in output` guard reported failure.
A diagnostic copy printed the actual bytes, proving the response was present.

## Repro
Compile `print(b"200 OK" in b"HTTP/1.1 200 OK")` through the generic object
membership path. The runtime's py_obj_contains has no bytes/bytearray branch.

## Test [CONFIRMED]
`tests/python/test_bytes_membership_runtime.py` failed before the change for
the C oracle. It covers bytes, bytearray, substring, integer byte, empty
substring and absent substring behavior.

## Proposals
- No.1 dispatch bytes/bytearray containment to the existing byte search [pending integration]

## No.1 dispatch bytes/bytearray containment to the existing byte search
### Code Change
Both runtime mirrors call py_bytes_find for bytes and bytearray containers.
This retains the existing search helper's supported needle surface. Invalid
needle error semantics are not extended by this bounded fix.

### Validation
Both runtime variants pass. The combined membership/classinfo suite passed
four cases in 139.60 seconds including isolated runtime construction.
The original dashboard still requires a fresh compile and execution.
