# Investigation: unary `-`/`~` on class instances crashed codegen (ClassType -> int coercion)

## Status
resolved

## Problem Description

Second of the four blocked-numpy-module root causes (sibling:
`generator-cpython-iteration-dominance.md`). `numpy/_utils/_pep440.py`
failed to compile with `Layer 1 cannot coerce ClassType to int`; file
bisection landed on `_cmpkey`'s `-Infinity` (unary minus on a module-level
`InfinityType()` instance). Minimal repro (6 lines): a class with
`__neg__`, `print(-t)` — CPython prints the dunder result; pcc's
`_emit_unary` routed `-`/`~` on ClassType operands into the numeric
`_to_int64` coercion and crashed.

## Repro

```bash
# class T: def __neg__(self): return 42
# t = T(); print(-t)
env -u LC_ALL uv run pytest tests/python/test_native_unary_dunder.py -q -n0
```

Observed red (2026-06-10): `PCC-PY-COMPILE-001 ... Layer 1 cannot coerce
ClassType to int` on the minimal repro and on the full `_pep440.py`.

## Test [CONFIRMED]

The minimal repro crash was observed; the regression
`tests/python/test_native_unary_dunder.py` now pins `-t` / `~t` /
`repr(-Infinity_instance)` against CPython values under strict
no-libpython self-backend.

## Proposals
- No.1 Dispatch `__neg__`/`__invert__` for ClassType operands before numeric coercion   [CONFIRMED]

## No.1 Unary dunder dispatch

### Code Change

`unary_call_lowering._emit_unary`: for `-`/`~` on a ClassType operand,
dispatch `__neg__`/`__invert__` via `_try_dispatch_dunder_unary` BEFORE
emitting the operand (the helper emits the receiver itself — dispatching
after the original `_emit_expr` would double-emit side effects). Unresolved
dunders raise a clear NotImplementedError naming the dunder instead of the
cryptic coercion message. `+obj` (`__pos__`) is NOT covered: the
pre-existing behavior returns the operand unchanged, which diverges from
CPython for classes without `__pos__` (TypeError there) — recorded, not
silently changed.

### CONFIRMED

Observed (2026-06-10): minimal repro compiles and prints 42 (auto-mode
binary run); FULL `numpy/_utils/_pep440.py` now compiles with zero errors
(first of the six blocked modules unblocked); focused regression -> 1
passed (42 / 7 / `-Infinity`). Battery + five-GC matrix results recorded
in `docs/current-goal-state.md`.

## Report

Proposal No.1 landed. Remaining blocked-module causes: unknown decorators
(x2), gnu.py arg-count mismatch (x1), generator-cpy guard (x2, clear
diagnostic by design pending frame support). The `__pos__` divergence and
dynamic (un-hinted) class unary dunders are recorded follow-ups.
