# Investigation: unrelated parking methods reject a concrete nonparking call

## Status
active

## Problem Description
The external gateway dashboard cannot compile: TaskScope.close parks, and the
effect checker rejects GatewayConnection.close and event cleanup calls that
share a method name with an unrelated parking class.

## Repro
`gtimeout 120s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 tests/python/test_vthread_gateway_regressions.py::test_concrete_nonparking_method_does_not_inherit_another_class_effect`

## Test [CONFIRMED]
The reduced Resource.close returns 42 without parking. A separate Parking.close
yields. Calling Resource.close after a yield rejects worker with
`unresolved user-method may park: .close` (2026-09-06).

## Proposals
- No.1 Resolve concrete method ownership before testing effect ambiguity [pending]

## No.1 Resolve concrete method ownership before testing effect ambiguity
### Code Change
Allow the existing concrete local-method resolver to resolve all known methods
when its effect-key filter is absent. In unresolved-effect diagnostics, use
that form and the existing sibling-method target resolver, including known
nonparking methods. Unknown receivers continue to fail closed.
### pending
Focused positive/negative regressions and native dashboard validation pending.

## References
The earlier virtual-thread resume ABI investigation is a separate defect:
[resume ABI](virtual-thread-resume-unboxes-args-for-boxed-worker.md).
