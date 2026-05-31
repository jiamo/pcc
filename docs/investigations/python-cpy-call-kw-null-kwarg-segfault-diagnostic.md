# Investigation: py_cpy_call_kw NULL kwarg value crashes inside PyDict_SetItemString — defensive diagnostic + upstream bug located

## Status
resolved (defensive guard); upstream comprehension+format kwarg bug located but not yet fixed

## Problem Description

The numpy executable produced by `pcc --backend self --python-libpython=auto`
(closing the prior class-method `self.functions` leak cap allowed the exe to
build at all) segfaulted at runtime with rc=139 and no stderr output. LLDB
showed the crash inside CPython's `PyDict_SetItemString + 156` called from
`py_cpy_call_kw + 180` inside `_pcc_py_module_top_numpy + 28360`:

```
EXC_BAD_ACCESS (code=1, address=0x0)
frame #0: Python`PyDict_SetItemString + 156
frame #1: exe6`py_cpy_call_kw + 180
frame #2: exe6`_pcc_py_module_top_numpy + 28360
frame #3: exe6`main + 188
```

Root cause: `py_cpy_call_kw` (`pcc/py_runtime/src/py_libpython.c:1322`) passes
`kw_vals[i]` straight to `PyDict_SetItemString(kwargs, kw_names[i], kw_vals[i])`.
When pcc-generated code emits a `py_cpy_call_kw` whose `kw_vals` array
contains a NULL pointer (a kwarg expression that silently evaluated to NULL
without setting an exception), CPython dereferences the NULL value and
crashes — with no diagnostic in the runtime telling the caller which kwarg
or call site went wrong.

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

`env -u LC_ALL uv run pcc --backend self --python-libpython=auto
--ir-scaffold=on /tmp/c8.py -o /tmp/c8.out && /tmp/c8.out`:
- Expected (CPython): `a a=msg_a / b b=msg_b`.
- Observed (pcc): `a <object tag=N> / b <object tag=N>` (both the same N,
  pcc's print rendering of a CPython fallback PyObject whose layout pcc
  doesn't natively decode). With the defensive guard the same shape now
  prints the offending kwarg name to stderr before bailing.

Variants:
- `c4`: `fmt.format(n="a", extended_msg="m")` with LITERAL kwargs → ✓.
- `c5`: same kwargs from plain `n = "a"; extended_msg = "m"` direct
  assignment (no loop) → ✓.
- `c6`: same as `c8` BUT with an intermediate `print` (or any positional
  `.format()`) inside the loop body before the kwarg `.format()` → ✓.
- `c7`/`c8`/`c9` (with/without a no-op touch of the locals): kwarg `.format()`
  from a for-loop unpack as the FIRST use of the unpacked locals → ✗.

So the trigger is narrow: a kwarg `.format(name=name, ...)` call inside a
for-loop unpack, in a libpython-fallback context (`--python-libpython=auto`),
where the unpacked locals are first used by that kwarg call. Adding any
prior use of those locals (print, positional format, etc.) avoids the bug,
which strongly suggests a marshal / box-conversion timing issue in the
fallback's kwarg-evaluation path.

In the numpy import path this exact shape appears in
`numpy/__init__.py:663`:

```python
{n: _msg.format(n=n, extended_msg=extended_msg)
 for n, extended_msg in _type_info}
```

## Test [CONFIRMED]

Defensive guard (this fix): no dedicated unit test added; the focused
class/exception/multi-file/generator suites (44 passed) plus the mandatory
self-host bootstrap (`test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
→ 1 passed in 37.18s) exercise the runtime change without regression.
End-to-end: the numpy auto-mode compile now runs further; the random
PyDict_SetItemString SIGSEGV is replaced by a clear stderr diagnostic
identifying the offending kwarg, after which numpy initialization continues
and hits the NEXT runtime layer (`AttributeError: __all__` in
`numpy/__init__.py:681`).

Upstream comprehension+format kwarg bug: not yet fixed — repro recorded
above and tracked as the next investigation.

## Proposals

- No.1 Defensive NULL-kw-value guard in py_cpy_call_kw  [CONFIRMED]
- No.2 Fix the upstream comprehension+kwarg-format fallback marshal bug  [pending — separate slice]

## No.1 Defensive NULL-kw-value guard in py_cpy_call_kw

### Code Change

`pcc/py_runtime/src/py_libpython.c::py_cpy_call_kw` — inside the
`for i in range(n_kw)` loop, check `kw_vals[i] == NULL` and bail with a
stderr diagnostic naming the offending kwarg + counts; decref the tuple and
the partial kwargs dict and return NULL. The caller (pcc-emitted code) sees
a NULL return and routes to its existing error-path handling.

### CONFIRMED

Root cause: `py_cpy_call_kw` did not validate `kw_vals[i]` before passing it
to CPython's `PyDict_SetItemString`, which dereferences the value without a
NULL check and crashes. The guard converts undefined-behavior crashes into
controlled error returns + an actionable stderr diagnostic identifying which
kwarg (by name) was NULL, which counts (n_kw, n_pos), and that the upstream
expression returned NULL without setting an exception.

For the numpy case this immediately produced the offending kwarg name
`extended_msg` and let the import proceed past the crash point to a clearer
downstream failure (`AttributeError: __all__` in `numpy/__init__.py:681`),
which is qualitatively a better error to investigate.

Evidence:
- Focused gates (`test_py_exceptions.py test_py_codegen_class_model.py
  test_py_multi_file_compile.py test_python_generator_parity.py -q -n0`)
  → 44 passed (no regression).
- Mandatory self-host gate
  `test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` → 1
  passed in 37.18s.
- Numpy auto-mode compile (real numpy site) → exe is produced; runtime
  now reports the offending kwarg names to stderr before bailing cleanly,
  and import proceeds to numpy/__init__.py:681 before its next failure.

## No.2 Fix the upstream comprehension+kwarg-format fallback marshal bug

### pending

Reduction matrix above pinpoints the trigger shape but not yet the codegen
or marshal path responsible. The next slice should compare the auto-mode IR
emitted for c6 (works) vs c8 (fails) under the libpython fallback path; the
diff will identify the marshal step that silently produces NULL when the
kwarg value is a for-loop-unpacked local without prior materialisation.
Likely candidates: the `_emit_call_kwargs` path in
`call_expression_lowering.py` boxing a value still in a "to-be-marshaled"
form, OR an ownership/refcount slot whose store hasn't been issued yet at
the kwarg-eval site.

## Report

Landed No.1 as a runtime-quality improvement that closes the immediate
"undiagnosable SIGSEGV" cap on the numpy auto-mode compile. The compile
now runs further and exposes a different runtime layer
(`AttributeError: __all__`). No.2 (the underlying comprehension+kwarg-format
bug) is the next investigation; its minimal repro c8 is recorded here and
in `docs/current-goal-state.md`.

Progress order: ... → (closed) class-method self.functions table leak →
exe PRODUCED → (closed, this) py_cpy_call_kw SIGSEGV → exe runs further →
(NEW) numpy/__init__.py:681 `AttributeError: __all__` runtime failure +
(separate) comprehension+kwarg-format marshal bug under fallback.
