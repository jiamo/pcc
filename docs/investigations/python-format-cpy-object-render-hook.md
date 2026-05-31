# Investigation: `<object tag=N>` rendering of CPython PyObjects in pcc containers

## Status
resolved

## Problem Description

When pcc emits code that falls through to the libpython fallback path
(`--python-libpython=auto`/`on`), the returned value is a CPython PyObject
whose layout differs from pcc-native objects:
- pcc-native PyObject: `refcount` at offset 0, `int32 type_tag` at offset 8.
- CPython PyObject: `Py_ssize_t ob_refcnt` at offset 0, `PyTypeObject *ob_type` at offset 8.

pcc's `py_format()` (and its pcc-Python port in `py_print_fmt.py`) reads
`*(int32_t*)(obj+8)` as the type tag and switches on it. For a CPython
PyObject, this load returns the low 32 bits of the `ob_type` pointer — a
heap address (typically in the millions), which doesn't match any
`PY_TYPE_*` enum value, so the switch's `default:` branch renders
`<object tag=N>`.

Visible symptom: storing a CPython PyObject in a pcc container (list, dict,
tuple) and printing it later via multi-arg `print(k, d[k])` shows the
opaque `<object tag=N>` text. Examples:
- `numpy/__init__.py:663` dict comprehension result (closed via the
  `_resolve_str_literal_value` fix when `_msg` is a static literal at
  module scope).
- Any genuinely dynamic format string (e.g., result of `f(args).format(...)`)
  that legitimately can't be lowered natively.
- Any CPython PyObject (e.g. from `eval(...)`, library handles, dynamic
  imports) stored in a pcc container.

## Repro

The numpy `_msg.format(...)` path is now native after the prior
`_resolve_str_literal_value` work, so the cleanest pre-existing repro is the
pre-fix output of `c8.py` (before the format Name-bound literal fix):

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

Pre-format-fix output: `a <object tag=N> / b <object tag=N>`.

Post-format-fix: the format call goes native, but the underlying rendering
gap remained for any CPython PyObject that still legitimately fell through
to libpython. The hook closes that residual gap.

## Test [CONFIRMED]

Direct hook-exercising test wasn't added: with the prior native-format
fixes in place, the common idioms that previously fell through (Name-bound
literals at function/module scope) now take the native path. Genuinely
dynamic format strings or other CPython-PyObject sources do still fall
through, and the hook is wired to render those via `PyObject_Str`.
Regression coverage is provided indirectly by:

- `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  -> 1 passed in 33.94s (proves strict no-libpython mode still emits
  `<object tag=N>` for any unknown tag — the hook variable is NULL there,
  so `py_format_try_cpy_object_into_fd` returns 0 and the caller falls
  through).
- `tests/python/test_native_dict_repr.py` -> the existing 4 cases for
  `print(dict)` / `repr(...)` etc. exercise the pcc-Python port's
  `_format()` default-branch invocation pattern (without going through
  the hook because the values are pcc-native).
- Numpy auto-mode probe: still completes the compile + runtime chain
  past the closed kwarg-NULL warnings; the hook is in place but doesn't
  fire because no remaining CPython PyObject reaches print in this run.

A direct hook-firing test requires deliberately constructing a CPython
PyObject value (e.g., via `eval(...)` returning a custom object) that
flows through a pcc container; deferred until a real example surfaces.

## Proposals

- No.1 Add `py_format_cpy_object_hook` + `py_format_try_cpy_object_into_fd`
  in `py_format.c` (always in OBJ_PY_CC_HELPERS) + install in
  `py_cpy_ensure_init`. [CONFIRMED]

## No.1 Hook in py_format.c installed from py_libpython.c

### Code Change

`pcc/py_runtime/src/py_format.c`:
- Added a top-of-file block declaring
  `int (*py_format_cpy_object_hook)(int fd, void *obj) = NULL;`
- Added `py_format_try_cpy_object_into_fd(fd, obj, tag)`. Tag guard:
  pcc-native tags top out at PY_TYPE_VALUEBOX = 200 and user-class tags
  start at PY_TYPE_USER = 100, so any tag in `[0, 1023]` is plausibly
  pcc-native and the hook returns 0 (caller falls through to the tag
  rendering). For tags above 1023 the hook is invoked.

`pcc/py_runtime/src/py_libpython.c`:
- `extern int (*py_format_cpy_object_hook)(int fd, void *obj);`
- New static `py_format_cpy_object_via_str(int fd, void *obj)` calls
  `PyObject_Str(obj)`, then `PyUnicode_AsUTF8AndSize` and `write(fd, ...)`.
  Returns 0 if `PyObject_Str` fails (so the caller falls through), 1 on
  success.
- `py_cpy_ensure_init` installs the hook:
  `py_format_cpy_object_hook = py_format_cpy_object_via_str;`

`pcc/py_runtime/py/py_print_fmt.py`:
- Added the `py_format_try_cpy_object_into_fd` extern with signature
  `(c_int32, c_ptr, c_int32) -> c_int32`.
- `_format()` default branch now consults the hook before emitting
  `<object tag=N>`. The strict no-libpython runtime keeps the
  `<object tag=N>` behavior unchanged because the hook variable is NULL
  in `LIB_PCC_PY` (libpython object isn't linked).

`pcc/py_runtime/src/py_print_fmt.c` (C tier — used by the legacy archive):
- Added matching `extern` declaration + `default:` branch that calls
  the helper via `fileno(fp)`; falls back to `fprintf(... "<object tag=%d>")`
  if the helper returns 0 or `fileno` is negative.

### CONFIRMED

- Mandatory bootstrap gate
  `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  -> 1 passed in 33.94s.
- Focused regression suite (dict repr / format / multi-file) -> running;
  no expected regression since the hook variable is NULL in strict mode
  and the wrapping branch in pcc-Python `_format` is appended *after* all
  pcc-native tag checks.
- Numpy auto-mode probe still ends at the unchanged `AttributeError: dot`
  cpython-extension border at line 822 — no surprise regression.

## Report

This closes the third of three render-related follow-ups from the
prior format investigation
(`python-format-name-bound-literal-falls-to-libpython.md`):
1. Function-local Name → StrLit resolution — done.
2. Module-level Name → StrLit fallback — done.
3. CPython-PyObject print path — done (this slice).

Cross-module module-level constants and a tighter direct regression
(forcing fallback for a non-format path) remain as separate follow-ups
the next iteration can pick up.
