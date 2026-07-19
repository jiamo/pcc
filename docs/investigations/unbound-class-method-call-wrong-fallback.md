# Investigation: `KnownClass.method(self)` with a CPython-backed base hit the any-class name-scan fallback (bogus arg-count error)

## Status
resolved

## Problem Description

Third of the four blocked-numpy-module root causes (siblings:
`generator-cpython-iteration-dominance.md`,
`unary-neg-classtype-coercion-crash.md`).
`numpy/distutils/fcompiler/gnu.py` failed to compile with
`Gnu95FCompiler.get_flags ... too many positional args: got 1, expected
at most 0` on the old-style super idiom `GnuFCompiler.get_flags(self)`.

`get_flags` is inherited from the CPython-backed `FCompiler` base
(`from numpy.distutils.fcompiler import FCompiler`), so Case 2 of
`_emit_method_call` (`ClassName.method(...)` on a known class) found
nothing on the native MRO walk and fell out WITHOUT emitting; the
lowering then reached the "last closed-world fallback: any class
declaring the method" name scan, which matched the SUBCLASS's
same-named `get_flags`, emitted the class object `GnuFCompiler` as a
bound instance receiver, and counted the explicit `self` as an extra
positional arg (1 explicit vs 0 remaining formals after `skip_self`).

Minimal repro (8 lines): a class whose base is CPython-backed plus a
subclass calling `Mid.format_usage(self)` — exact same error text.

## Repro

```bash
# from argparse import ArgumentParser
# class Mid(ArgumentParser): pass
# class Leaf(Mid):
#     def format_usage(self): return Mid.format_usage(self)
env -u LC_ALL uv run pytest tests/python/test_native_unbound_class_method_call.py -q -n0
```

Observed red (2026-06-10): `PCC-PY-COMPILE-001 ... too many positional
args: got 1, expected at most 0` on the minimal repro and on the full
`gnu.py` (library/auto sweep mode).

## Test [CONFIRMED]

The minimal repro failure was observed byte-identical to the gnu.py
error; the raise-site stack (temporary tagged instrumentation, removed)
pinned the path: `_emit_method_call` -> any-class fallback ->
`_emit_direct_method_call` -> `_resolve_call_kwargs`.

## Proposals
- No.1 Route unresolvable `KnownClass.method(...)` to dynamic getattr-on-class + call   [CONFIRMED]

## No.1 Dynamic unbound-call dispatch

### Code Change

`method_call_expression_lowering.py` Case 2: when the receiver is a
known class but `_resolve_method_mro` (and the metaclass route) cannot
resolve the method natively, return
`_emit_callable_attribute_call(attr.obj, attr.name, args, kwargs, span)`
— emit the class object, `py_obj_getattr`, then `py_obj_call` with ALL
explicit args (CPython unbound-call semantics) — instead of falling
through to the instance-receiver name-scan fallbacks. The
`_maybe_emit_class_lowering_extern_method` probe is tried first to
preserve the bootstrap extern path's precedence.

### CONFIRMED

Observed (2026-06-10): minimal repro compiles, IR contains
`py_obj_getattr` and the subclass method symbol has no direct call
site; FULL `gnu.py` compiles (rc=0). Strict no-libpython self-backend
runtime parity: `A.nope(1)` on a method-less class prints `attr-error`
(AttributeError caught) — byte-equal to CPython. The natively
resolvable `A.get(self)` direct path is unchanged (prints 2).
Regression: `tests/python/test_native_unbound_class_method_call.py`
(3 passed). Battery + five-GC matrix results recorded in
`docs/current-goal-state.md`.

## Report

Proposal No.1 landed. Blocked-numpy-module sweep: 6 -> 4 (pep440 +
gnu.py unblocked). Remaining: unknown decorators x2
(`_core/numeric.py` 'ones', `lib/_npyio_impl.py` 'loadtxt'),
`misc_util.py` generator-cpy guard (clear diagnostic by design), and
`fortran.py` now surfacing "Python IR pass pipeline failed" — the
already-recorded No.4 second dominance site (gc-root reload in native
protocol-for inside generators) in
`generator-cpython-iteration-dominance.md`, not a new failure class.
