# Investigation: hasattr() on a native-builtin module statically folds to False for any attr not in pcc's hardcoded attr table

## Status
resolved (the static-False fold is removed; runtime path handles it)

## Problem Description

`hasattr(os, "__all__")` (and any other attribute pcc doesn't list statically)
evaluates to **False at compile time** under `--python-libpython=auto`, even
though the underlying CPython `os` module has `__all__`. The IR for the
hasattr expression is a literal `zext i1 false to i32` — no runtime call
happens at all.

Found while investigating the post-class-method-leak numpy run, which
reaches `numpy/__init__.py:681 set(_core.__all__)` and fails with
`AttributeError: __all__`. That specific numpy line is *direct* attribute
access, not `hasattr`, so this hasattr bug is not the proximate cause of
the numpy failure — but it is a real correctness bug in the same area
(pcc treats compile-time static-attribute knowledge as authoritative when
it shouldn't be under libpython fallback).

## Repro

```python
import os
def main() -> None:
    print("path:", hasattr(os, "path"))
    print("getcwd:", hasattr(os, "getcwd"))
    print("__all__:", hasattr(os, "__all__"))
    print("__name__:", hasattr(os, "__name__"))
    print("nonexistent_xyz:", hasattr(os, "nonexistent_xyz"))
main()
```

Before fix (`--python-libpython=auto`): every line prints `False` — including
the four attributes CPython's `os` module actually has. After fix: the
static-False fold is gone, so the IR now calls `py_obj_getattr` (or the
cpython fallback when applicable) at runtime. For `os` specifically the
result is still False because pcc's `os` module reference is a native handler
with no backing PyObject for runtime getattr; but for any object pcc tracks
as a cpython value (modules loaded via libpython fallback) the runtime path
can now answer correctly.

## Test [CONFIRMED]

Focused regression: 60 passed across
`test_py_codegen_class_model.py test_py_for_generic_iterable.py
test_native_os_misc.py -q -n0`. Mandatory self-host bootstrap
(`test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`) → 1
passed in 39.05s. No new regression test was added in this slice — the
hasattr surface for native-builtin modules without runtime backing is
unchanged (still False, just via the runtime path), so existing tests
already cover the regression surface; an `_expr_looks_cpython=True` case
that would show the fix's positive effect requires a multi-file harness that
this slice did not set up.

## Proposals

- No.1 Remove the static-False fall-through for unknown native-table attrs  [CONFIRMED]

## No.1 Remove the static-False fall-through for unknown native-table attrs

### Code Change

`pcc/py_frontend/codegen/native_modules.py::_maybe_emit_native_module_hasattr`
— when the attribute is not in `native_table[module_name]` and not in
`_native_builtin_module_has_attr`, return `None` (fall through) instead of
`ir.Constant(_I1, 0)`. The caller in
`call_expression_lowering.py::_emit_hasattr` then takes the next branch —
either the libpython call to `builtins.hasattr` (when the object also looks
like a cpython value) or `py_obj_getattr` non-NULL (otherwise).

### CONFIRMED

Root cause: `_maybe_emit_native_module_hasattr` historically returned
`ir.Constant(_I1, 1 if present else 0)` for any object pcc recognised as a
native module, where `present = attr in native_table[mod] or
_native_builtin_module_has_attr(mod, attr)`. pcc's `native_table` and
`_native_builtin_module_has_attr` are intentionally narrow (math: pi/e/tau
plus a few; codecs: BOM_*; pcc: valueclass; os: nothing) — pcc doesn't
enumerate every attribute of every Python module. Returning the negative
answer authoritatively turned compile-time "I don't statically know"
into runtime "definitely False," breaking
`hasattr(os, "__all__"|"__name__"|"path"|"getcwd"|...)` under
`--python-libpython=auto`.

The fix removes the negative-answer return only; the positive answer
(attr present in static table) still returns True statically, since that is
genuinely authoritative. Net effect: pcc no longer LIES that an attribute
doesn't exist — it defers to the runtime, which either answers correctly via
libpython for cpython-backed values, or returns False via py_obj_getattr
for objects with no backing (same as before for the native-only case).

Evidence:
- Focused suites (60 passed).
- Mandatory bootstrap (1 passed in 39.05s).
- Numpy auto-mode compile is unchanged (the line 681 failure is direct
  attribute access `_core.__all__`, not `hasattr`, and is gated by a
  separate module-level-constant-export bug — out of scope for this slice).

## Report

Landed No.1, a 1-line behavioral change ("return None on miss instead of
constant False"). Does not close the numpy line 681 blocker, which needs
module-level constant exposure from sibling-compiled modules. Records a
real correctness improvement to the hasattr lowering that surfaced while
triaging the numpy runtime layer.
