# Investigation: bare imported decorators crashed function declaration (`@finalize_array_function_like`)

## Status
resolved

## Problem Description

Last of the four blocked-numpy-module root causes (siblings:
`generator-cpython-iteration-dominance.md`,
`unary-neg-classtype-coercion-crash.md`,
`unbound-class-method-call-wrong-fallback.md`).
`numpy/_core/numeric.py` and `numpy/lib/_npyio_impl.py` failed with
`Layer 1 does not handle decorators; received 2 on 'ones' (first
unrecognised: finalize_array_function_like)`.

The decorator policy already had an imported-decorators-are-metadata
principle (`_decorator_is_external_metadata_factory`): call-shaped
`@imported_factory(...)` (e.g. `@array_function_dispatch(_d)`,
`@set_module('numpy')`) is treated as compile-time metadata because pcc
cannot execute arbitrary import-time decorator factories, while the
underlying function body still compiles. The BARE shape
`@imported_name` (numpy's `@finalize_array_function_like`, stdlib's
`@functools.singledispatch`) was not covered — `isinstance(dec, Call)`
gated the whole helper — so declaration raised NotImplementedError.

This is a generic-mechanism gap (the minimal repro uses only stdlib
`functools.singledispatch`), not a numpy special case. Table probe
(temporary tagged instrumentation, removed): both
`finalize_array_function_like` and `singledispatch` live ONLY in
`_cpy_module_env`.

## Repro

```bash
# from functools import singledispatch
# @singledispatch
# def f(x): return x + 1
env -u LC_ALL uv run pytest tests/python/test_py_function_decorators.py -q -n0
```

Observed red (2026-06-10): `Layer 1 does not handle decorators ...
(first unrecognised: singledispatch)` on the 6-line minimal repro,
identical class to both numpy modules.

## Test [CONFIRMED]

The minimal repro failure was observed; CPython prints 2 (the
undecorated call semantics of `f(1)` are unchanged by ignoring the
dispatch registration for a direct call).

## Proposals
- No.1 Extend the imported-metadata principle to bare cpy-imported decorator names   [CONFIRMED]

## No.1 Bare cpy-imported decorators are metadata

### Code Change

`decorator_lowering._decorator_is_external_metadata_factory`: a bare
`Name` decorator qualifies as compile-time metadata IFF the ident is a
CPython-imported symbol (`_cpy_module_env`) and NOT a same-module pcc
function (`self.functions`) — deliberately narrower than the call shape
(which also accepts cross-module defs / module globals / native
aliases), because a bare module-global or pcc-function decorator is
semantic user code that must keep applying. Docstring records both
shapes and the boundary.

### CONFIRMED

Observed (2026-06-10): minimal repro compiles; auto-mode binary prints
2 == CPython (underlying function stays directly callable). Boundary
pin: a same-module bare decorator (`@double_result`) still APPLIES for
real (8 == CPython, unchanged). FULL `numpy/_core/numeric.py` AND
`numpy/lib/_npyio_impl.py` compile (rc=0) — blocked-module sweep
6 -> 1 (only `distutils/misc_util.py` remains, the by-design
generator-cpy clear diagnostic, No.3 in the dominance investigation).
Regressions added to `tests/python/test_py_function_decorators.py`
(6 passed). Battery + five-GC matrix in `docs/current-goal-state.md`.

## Report

Proposal No.1 landed. HONEST LIMIT: ignoring an imported decorator is
the recorded, pre-existing design tradeoff — the decorator's runtime
wrapping (e.g. singledispatch's `register` dispatch, numpy's `like=`
dispatch) does NOT take effect in compiled code; the underlying
function's direct-call semantics are preserved. Code that semantically
depends on the wrapper still needs the cpy fallback path. All four
blocked-numpy-module root causes are now dispositioned: pep440
(unary dunder), gnu.py (unbound call), fortran.py (generator native
iter frame slot), numeric.py + _npyio_impl.py (this fix); misc_util.py
remains on the recorded No.3 design boundary.
