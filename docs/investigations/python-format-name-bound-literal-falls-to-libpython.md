# Investigation: `name = "<literal>"; name.format(...)` falls to libpython fallback even though the format string is statically a literal

## Status
resolved

## Problem Description

`_maybe_emit_literal_str_format` (`pcc/py_frontend/codegen/format_lowering.py:116`) gates its compile-time parsed native format path on `isinstance(attr.obj, StrLit)` — i.e. the format string must be a literal AT THE CALL SITE. The common Python idiom `fmt = "{n}={x}"; fmt.format(n=..., x=...)` (and the dict-comprehension form `{k: fmt.format(...) for k in ...}`) binds the format string to a local Name first; the call site sees a `Name`, not a `StrLit`, and the fast path returns `None`. The lowering then falls through to the libpython `py_cpy_str_format` / `py_cpy_call_kw` fallback.

The fallback produces a CPython `PyStr` object (CPython object layout — `ob_refcnt` at offset 0, `ob_type` pointer at offset 8). pcc's print/str runtime decodes pcc-native objects by reading `*(int32*)(obj+8)` as `enum PyTypeTag`; for a CPython object that load returns the low 32 bits of `&PyUnicode_Type` (a heap address in the millions), which doesn't match any `PY_TYPE_*` enum, so `py_format` falls through its switch's `default:` and renders `<object tag=N>`. The value is correct (different objects per call; intermediate single-argument `print(s)` works because pcc's print for a single CPython arg threads through libpython's `PyObject_Str`); the visible bug is that *containers-of-CPython-PyStr* re-render as `<object tag=N>` on retrieve+print, and `str(s)` returns `<null>` because pcc's `py_obj_str` no-libpython path also doesn't know the layout.

In numpy this exact shape appears at `numpy/__init__.py:663`:

```python
{n: _msg.format(n=n, extended_msg=extended_msg) for n, extended_msg in _type_info}
```

with `_msg` bound to a string literal earlier in `numpy/__init__.py`'s module scope. Prior investigation (`python-cpy-call-kw-null-kwarg-segfault-diagnostic.md`) had labelled this a "marshal bug" / "NULL kwarg value"; my reduction matrix (c8 through c22 below) shows the actual mechanism is "format takes the libpython fallback because the format string is bound to a Name, and the fallback's return value pcc can't render via the containers-print path".

## Repro

```python
def main() -> None:
    items = [("a", "msg_a"), ("b", "msg_b")]
    fmt = "{n}={extended_msg}"
    d = {}
    for n, extended_msg in items:
        d[n] = fmt.format(n=n, extended_msg=extended_msg)
    for k in sorted(d.keys()):
        print(k, d[k])
```

Before the fix:
```
a <object tag=55163008>
b <object tag=55163008>
```

After the fix (same `N` tag, since both are CPython `PyStr` whose type pointer truncation matches — but actual values are distinct, verified via `==` comparison and `str(...)` calls in c20):
```
a a=msg_a
b b=msg_b
```

Reduction matrix (auto-mode, `--python-libpython=auto`):
- c8: kwarg `.format(...)` from for-loop unpack, inline as dict value → ✗ (now ✓).
- c14: same kwargs with intermediate local + `print(s)` then dict store → intermediate print correct (single-arg fallback uses libpython for str); final dict-retrieved print still ✗ (now ✓).
- c15: `s = n + "=" + extended_msg` (no format) + dict store → ✓ (native concat returns pcc-native PyStr; never went through fallback).
- c16/c17: two distinct local format calls (no for-loop unpack); intermediate prints work; dict retrieval shows `<object tag=N>` → ✗ (now ✓).
- c19: two distinct locals stored in list, no rebind → list elements both rendered as `<object tag=N>` (same N = `&PyUnicode_Type` low bytes — both are PyStr) → ✗ (now ✓).
- c20: with `str(...)` and iteration → `seen[0] == seen[1]` evaluates to `False`, confirming distinct CPython PyStr objects (the render is the bug, not the storage).
- c21: dict-comprehension form `{n: fmt.format(...) for ...}` (matches numpy/__init__.py:663) → ✗ (now ✓).
- c22: literal call site `"{n}={x}".format(...)` (no Name indirection) → ✓ (the existing fast path).

## Test [CONFIRMED]

`tests/python/test_native_str_format_index.py::test_str_format_name_bound_literal_takes_native_path` — 1 passed in 0.84s. Differential vs CPython `subprocess.run([sys.executable, src])`; asserts identical stdout for both the for-loop and the dict-comprehension shapes.

## Proposals

- No.1 Extend `_maybe_emit_literal_str_format` to accept `Name` references when the function body contains exactly one `name = <StrLit>` Assign and no other rebind.  [CONFIRMED]

## No.1 Extend `_maybe_emit_literal_str_format` to accept Name → StrLit

### Code Change

`pcc/py_frontend/codegen/format_lowering.py`:

1. Imports add `Assign`, `AugAssign`, `ClassDef`, `For`, `FuncDef`, `If`, `Name`, `Try`, `While`, `With`.
2. New helper `_resolve_str_literal_value(expr)`:
   - `StrLit` → returns `expr.value`.
   - `Name` → walks the current function body (via `_iter_function_str_lit_bindings`); requires exactly one Assign binding `target_name → StrLit` and no other rebind to that name.
   - Other → returns `None`.
3. New helper `_iter_function_str_lit_bindings(body, target_name)`:
   - Iterative walker over `body` (and transitively `If` / `While` / `For` / `Try` / `With` bodies/handlers/finally) without descending into nested `FuncDef` / `ClassDef`.
   - Yields the literal value for each matching Assign; yields `None` and returns when an AugAssign, a non-StrLit Assign, a For-target match, an except-handler name match, or a With-as match is encountered for `target_name`.
4. `_maybe_emit_literal_str_format` now calls `_resolve_str_literal_value(attr.obj)`; behaviour unchanged for the prior StrLit-at-call-site shape.

### CONFIRMED

- New regression `test_str_format_name_bound_literal_takes_native_path` -> 1 passed (differential vs CPython; covers both for-loop and dict-comprehension forms of c8/c21).
- Pre-existing native-format gates remain green:
  - `test_native_str_format_index.py` -> 3 passed.
  - `test_native_format_hex_alt.py` + `test_py_string_percent_format.py` -> 6 passed in 5.97s (combined).
- Mandatory self-host gate `test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 41.61s.

## Report

The "comprehension+kwarg-format marshal bug" lineage in
`docs/investigations/python-cpy-call-kw-null-kwarg-segfault-diagnostic.md` and the
`docs/current-goal-state.md` entries from 2026-05-28 17:30 (the "IR is
byte-identical between c7 and c14") was a misframing. The IR-text equivalence
claim was probably correct WITHIN the slice that the prior investigation
diff'ed — both shapes go through the libpython fallback path with the same
sequence of `py_cpy_*` calls. The user-visible divergence comes from a
DIFFERENT layer: pcc's print / str / container-render path for CPython objects
doesn't know how to surface the value, so containers-of-fallback-PyStr show
`<object tag=N>`. The fix avoids the fallback entirely for the common idiom
where the format string is a local-bound literal; in cases that still go
through the fallback (genuinely dynamic format strings, e.g. assembled at
runtime), the print path remains a separate gap and is the natural next slice.

Follow-ups (NOT done in this slice):
- Module-level `MODULE_STRING_CONST = "..."` referenced inside any function in
  the module. Currently `_resolve_str_literal_value` only walks the current
  function's body; the same idiom across function boundaries still falls
  through. The actual `_msg` in `numpy/__init__.py:663` is module-level (need
  to verify which scope), so a follow-up should extend the resolution to also
  consult a module-level constant table populated during the declare pass.
- Generic "print of CPython PyObject" rendering: when a CPython PyObject is
  unavoidably stored in a pcc container, pcc's `py_format` `default:` branch
  could route through `PyObject_Str` via the existing libpython linkage
  instead of emitting `<object tag=N>`. Separate slice.

## Update — 2026-05-29 module-level fallback

The module-level follow-up above is now CLOSED. `_resolve_str_literal_value`
gained a fallback: when the current function body has no binding for the
target Name (verified via the new `_body_binds_name` walker, so a function-
local shadow correctly disables the fallback), the resolver walks
`self.ast_module.body` with the same `_iter_function_str_lit_bindings` walker
(descends into top-level If/Try/With/While/For without entering nested
FuncDef/ClassDef). Verified end-to-end:

- New regression `test_str_format_module_const_literal_takes_native_path` -> 1 passed in 0.96s
  (mirrors numpy's exact shape: `_MSG = (multi-line literal)` inside `if not
  False:`, dict-comprehension uses `_MSG.format(...)` at module init).
- Pre-existing format / multi-file suites -> 33 passed in 28.49s (no regression).
- Mandatory self-host gate -> 1 passed in 46.57s.
- **Numpy auto-mode probe (real numpy site)**: the 4× `py_cpy_call_kw: NULL
  kwarg value at i=1 name=extended_msg` warnings from `numpy/__init__.py:663`
  are NOW GONE. The dict-comprehension at line 663 takes the native compile-
  time-parsed format path; the libpython fallback that produced the NULL
  kwargs is no longer exercised. Remaining downstream blocker is unchanged:
  `AttributeError: dot` at `_sanity_check` line 822 (the cpython-extension
  boundary; needs `_multiarray_umath` C extension which is documented out of
  scope for B-P0-PKG).
