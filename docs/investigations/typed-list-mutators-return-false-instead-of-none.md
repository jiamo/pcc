# Investigation: typed list mutators return False instead of None

## Status

resolved

## Problem Description

The typed-list lowering path returns LLVM `i1 0` after void list mutators,
while the dynamic-list path returns the `py_None` singleton.  When the method
call is used as an expression, the typed value is boxed as `False`, violating
Python semantics.  The runtime mutation itself succeeds.

This was exposed by the ordinary-semantics gate for the GC4 list-clear
transaction and is independent of GC backend behavior.

## Repro

```bash
gtimeout 120s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_python_list_methods_parity.py::test_list_clear
```

Expected CPython-compatible output: `0 True` then `4 1`.

Observed on 2026-08-24: compiled native/no-libpython program prints `0 False`
then `4 1`; pytest exits 1.

## Test [CONFIRMED]

The focused test deterministically proves both halves: list contents are
cleared and reusable, but the expression result is the wrong singleton/value.
Source inspection locates `return ir.Constant(_I1, 0)` in the typed path for
append, extend, insert, remove, clear and reverse, while the matching dynamic
path calls `_emit_none_literal()`.

## Proposals

- No.1 Return the None singleton from every typed void list mutator [pending]

## No.1 Return the None singleton from every typed void list mutator

### Code Change

Replace only the six typed-list mutation success placeholders with
`self._emit_none_literal()`, matching the already-correct dynamic path.  Extend
the focused compiled regression to assert `is None` for append, extend, insert,
remove, clear and reverse while also checking their mutations.  Do not change
boolean-returning operations or pop/index/count/copy result lowering.

### CONFIRMED

All six typed success returns now use `_emit_none_literal()`.  The focused
compiled program observes `is None` for append, extend, insert, remove, reverse
and clear while checking each mutation, and the complete list parity file is
green.  Bootstrap identity plus fallback/IR fallback ratchets are also green.

The broader multi-file suite encounters a known old direct-store IR assertion.
A candidate-off A/B reproduces it identically, so it is not attributed to this
proposal.  Exact commands, hashes and the routed limitation are in
`docs/goal/evidence/2026-08-24-typed-list-mutator-none-results.md`.

## Report

Proposal No.1 is the complete fix: typed and dynamic list paths now agree on
the Python `None` result without changing non-void method lowering or runtime
mutation behavior.  No alternative proposal or baseline relaxation was used.
