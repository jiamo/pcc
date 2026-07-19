# Investigation: pcc1 native JSON string escapes

## Status
resolved locally 2026-06-05.

## Problem Description
The GC bootstrap hot-path investigation found that reusing lifted ASTs through
JSON would remove most of the repeated codegen-worker parse/lift time, but the
experiment failed correctness under pcc1. The reduced failure is not a GC
algorithm problem: native `json.loads` preserved JSON string escape bytes
instead of decoding them, so strings containing newlines, quotes, or
backslashes changed value after `json.dumps` / `json.loads`.

This matters for pcc1 because stage2 emits host-code payloads through JSON. A
literal `\n` in generated host code is different from a real newline, and can
turn valid code into a `python3 -c` syntax error.

Related predecessor: `gc0-bootstrap-runtime-hotpaths-2026-06-05.md`, proposal
No.6.

A later AST-wire replay found two more native JSON gaps on the same path:

- `json.dumps` emitted raw control characters such as vertical-tab/form-feed
  instead of escaping every JSON-disallowed byte below `0x20`.
- numeric AST fields containing `Infinity`, `-Infinity`, or finite floats
  could not round-trip through native `json.loads`.

## Repro
Before the fix, this strict no-libpython runtime regression failed
deterministically:

```text
env -u LC_ALL uv run pytest \
  tests/python/test_python_module_imports_parity.py::test_import_json_string_escape_roundtrip \
  -q -n0

AssertionError:
assert ['False', 'False', 'False', 'False', 'False'] ==
       ['True', 'True', 'True', 'True', 'True']
```

A pcc1-built probe had the same symptom:

```text
{"x": "line1\nline2", "q": "a\"b", "slash": "a\\b"}
False
False
False
line1\nline2
```

The final line is one physical output line containing the literal bytes
backslash + `n`, not two lines separated by a newline.

## Test [CONFIRMED]
The failing test added for this investigation is:

```text
tests/python/test_python_module_imports_parity.py::test_import_json_string_escape_roundtrip
```

It compiles a no-libpython program using native `json.dumps` / `json.loads` and
checks newline, quote, backslash, tab, and carriage-return roundtrips against
CPython string values.

The follow-up numeric regression is:

```text
tests/python/test_python_module_imports_parity.py::test_import_json_float_roundtrip
```

It checks finite float and `Infinity` / `-Infinity` round-trips in native
no-libpython JSON.

The pcc1 smoke gate was extended too:

```text
tests/python/test_pcc1_python_smoke.py::test_pcc1_smoke_json_loads
```

It runs only when an existing pcc1 binary is present, but proves the same
runtime path when a pcc1-compiled program links the current native runtime.

## Proposals
- No.1 decode JSON string escapes in `py_json.c` [CONFIRMED]
- No.2 escape control characters and round-trip JSON floats [CONFIRMED]

## No.1 decode JSON string escapes in `py_json.c`

### Code Change
`pcc/py_runtime/src/py_json.c::json_parse_string()` now builds decoded output
instead of returning the raw slice between quotes.

Supported escapes:

```text
\" \\ \/ \b \f \n \r \t \uXXXX
```

`\uXXXX` is emitted as UTF-8, including valid surrogate pairs. Invalid escapes,
unpaired surrogates, and unescaped control characters now fail parsing instead
of silently producing a corrupted string.

### CONFIRMED
The focused no-libpython regression now passes:

```text
env -u LC_ALL uv run pytest \
  tests/python/test_python_module_imports_parity.py::test_import_json_string_escape_roundtrip \
  -q -n0

1 passed in 5.81s
```

The pcc1 smoke gate also passes on this host:

```text
env -u LC_ALL uv run pytest \
  tests/python/test_pcc1_python_smoke.py::test_pcc1_smoke_json_loads \
  -q -n0

1 passed in 25.90s
```

The change does not touch GC barriers, object ownership, self-backend lowering,
or libpython fallback policy. It only fixes the native runtime JSON string
semantics that blocked AST-wire reuse.

## No.2 escape control characters and round-trip JSON floats

### Code Change
`pcc/py_runtime/src/py_json.c` now:

- escapes `\b`, `\f`, and any remaining control character below `0x20` during
  string dumping,
- parses finite JSON numbers as ints or floats depending on syntax,
- accepts and dumps `NaN`, `Infinity`, and `-Infinity` in the same compatibility
  shape used by CPython's default `json` module.

### CONFIRMED
Focused native JSON regressions pass:

```text
env -u LC_ALL uv run pytest \
  tests/python/test_python_module_imports_parity.py::test_import_json_string_escape_roundtrip \
  tests/python/test_python_module_imports_parity.py::test_import_json_float_roundtrip \
  -q -n0

2 passed in 6.59s
```

The pcc1 smoke gate was re-run with the string escape extension:

```text
env -u LC_ALL uv run pytest \
  tests/python/test_pcc1_python_smoke.py::test_pcc1_smoke_json_loads \
  -q -n0

passed in the focused JSON smoke batch
```

## Report
Proposals No.1 and No.2 landed. This closes the reduced native JSON semantic
blockers found while profiling the remaining full-bootstrap GC matrix cost.
It does not make AST wire a performance win: after these fixes, AST wire
preserved GC4 `pcc2`/`pcc3` byte identity but regressed focused GC4 wall time,
so `PCC_PY_FRONTEND_AST_WIRE` remains opt-in and disabled by default.
