# Investigation: NumPy exposes SetType coercion and tuple-unpack lowering failures

## Status
resolved locally (2026-07-21)

## Problem Description

After target-Python-aware acquisition selects NumPy 2.4.6 and builds its two
eager pcc-native extensions, strict pcc1/self/no-libpython compilation fails in
the pure-Python package closure. `numpy`, `numpy._core.einsumfunc`, and
`numpy.lib._npyio_impl` expose `SetType` values to code paths that assume an
integer condition, concrete tuple RHS, or statically typed peer operand. The
same current-source compiler also fails against the pinned NumPy 2.4.4 site;
the difference was compiler version, not NumPy version.

## Repro

Compile a program containing `import numpy as np` and basic array addition
against either the freshly installed 2.4.6 site or the pinned 2.4.4 site using
the current compiler with `--backend self --python-libpython=off
--ir-scaffold=on`. Before the fixes it exits 1 at the first unsupported set
shape.

## Test [CONFIRMED]

Package-neutral regressions now cover `list(set)`, set tuple-unpack and arity
errors, `dict.keys() | set`, and in-place set operations whose peer is dynamic.
The current compiler completes the pinned NumPy 2.4.4 import-and-array-add
closure in about 24 seconds. Runtime validation then exposed a separate module
attribute lookup failure, tracked in
`python-module-attrs-loses-extension-binding.md`.

## Proposals

- No.1 Fix the minimized SetType lowering boundaries [done]

## No.1 Fix the first minimized SetType lowering boundary

### Code Change

The frontend now treats sets as iterables for `list()` and tuple unpacking,
lowers real `dict.keys()` views to a set for set operators, and uses checked
set helpers when exactly one operand is statically dynamic. In-place operators
preserve the left set's identity. The changes are generic; there is no package
name dispatch or source rewriting.
