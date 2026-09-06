# Investigation: native generator frame planning misses nested expressions

## Status
active

## Problem Description
Host pcc compiles the gateway, while pcc1 reports a missing child continuation
slot for `if not virtual_thread.call(connection.lifecycle.admit_upstream)`.

## Repro
`/tmp/pcc_gateway_call_not.py` reduces the failure to a yielding function and
`if not vt.call(yes)`. The v4 Stage1 candidate reports no planned delegation
slots. The expected compiled output is 42.

## Test [CONFIRMED]
The native v4 reproduction failed. The retained tests in
`tests/python/test_vthread_gateway_regressions.py` cover a negated parking
call and the AST walker with CPython dataclass reflection removed. Before
the change the latter returned an empty tuple instead of visiting `operand`.

## Proposals
- No.1 use the existing shared AST field schema [pending native qualification]

## No.1 use the existing shared AST field schema
### Code Change
The generator-local walker previously relied on `__dataclass_fields__` for
expressions other than Call, Name and TupleExpr. Native dataclasses do not
publish that CPython dictionary. Delegate Expr and remaining Stmt discovery
to hoist_analysis's explicit field schema, covering all known expression
forms without special-casing gateway code.

### Validation
The two focused host tests passed in 1.75 seconds. A newly built pcc1 and the
original dashboard compilation remain required.
