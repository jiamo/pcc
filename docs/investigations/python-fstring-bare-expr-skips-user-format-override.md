# Investigation: `f"{x}"` lowered to `str(x)` skips user `__format__` override

## Status
resolved

## Problem Description

`tests/python/test_format_protocol.py::test_fstring_forwards_spec`
failed with:

```python
class Tagged:
    def __format__(self, spec):
        return "[" + spec + "]"

x = Tagged()
print(f"{x}")        # expected "[]", got "<null>"
print(f"{x:wide}")   # "[wide]" — OK
print(f"{x:>10}")    # "[>10]"  — OK
```

`f"{x}"` printed `<null>` instead of `[]`, while the same f-string
with an explicit spec (`f"{x:wide}"`) worked correctly.

Root cause: pcc's parser strips the `_FStringFormat` wrapper for
bare-expression f-string parts (no `!` conversion, no `:` spec), and
`pcc/parse/py_lift.py::_e_FString` then wraps every non-text part in
`str(inner)`. That short-circuits CPython's actual `f"{x}"` semantics,
which is `format(x, "")` — i.e., dispatch to `__format__("")` and only
fall back to `__str__` when `__format__` is the default
`object.__format__`. For a class that overrides `__format__` but not
`__str__`, pcc was calling the default `__str__` (which on Tagged
returns NULL because no `__str__` is defined and the default object
str path doesn't apply), printing `<null>` from the runtime
print-format path.

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_format_protocol.py::test_fstring_forwards_spec \
  -q -n0
```

Pre-fix: `assert ['<null>', '[wide]', '[>10]'] == ['[]', '[wide]', '[>10]']`.

## Test [CONFIRMED]

Same pytest case; pre-fix fails, post-fix passes.

## Proposals

- No.1 Patch `_e_FStringFormat` empty-spec branch                    [PARTIAL]
- No.2 Patch `_e_FString` to wrap parts in `format(inner, "")`       [CONFIRMED]

## No.1 Patch `_e_FStringFormat` empty-spec branch
### Code Change
Replace the empty-spec `str(inner)` wrap inside `_e_FStringFormat`
with `format(inner, "")`.

### PARTIAL — necessary but insufficient
The parser strips the `_FStringFormat` wrapper for bare-expr cases
(parser-side optimization at `pcc/parse/py_parse.py` line 2031: if
`debug_prefix is None and conversion is None and format_spec is None:
return expr`). So `_e_FStringFormat` is not called for `f"{x}"`, and
the `str(...)` wrap that bites is the one in `_e_FString`'s part
loop.

## No.2 Patch `_e_FString` to wrap parts in `format(inner, "")`
### Code Change

`pcc/parse/py_lift.py::_e_FString`:

```python
for part in e.parts:
    if type(part) is pp._FStringText:
        ...
        continue
    inner = self.lift_expr(part)
    pieces.append(
        pa.Call(
            span,
            pa.StrType("str"),
            pa.Name(span, _DYN, "format"),
            (inner, pa.StrLit(span, pa.StrType("str"), "")),
            (),
        )
    )
```

### CONFIRMED
- `test_format_protocol.py` 7 / 7 (was 6 passed / 1 failed).
- `test_py_corpus.py` + `test_native_str_format_index.py` +
  `test_py_string_percent_format.py` + `data_model` — 263 passed
  (only pre-existing `test_t1_metaclass_type_enum_abcmeta_compiled`
  failing, unrelated enum/metaclass slice).
- Fallback baselines unchanged: 17 passed, 4 skipped.

### Why this is correct
The pcc runtime `py_obj_format(o, spec)` first tries `__format__`,
then falls back to `py_obj_str` for `spec == NULL || spec == py_None
|| spec[0] == '\0'`. So `format(x, "")` is the right call shape for
both classes with custom `__format__` (which now runs) and plain
objects (which still get `str(x)` via the runtime fallback). The
parser-side `_FStringFormat`-stripping optimization had assumed
`str(x) == format(x, "")`, which is only true when `__format__` is
not overridden.

## Report
Landed via a single ~10-line edit to `pcc/parse/py_lift.py::_e_FString`.
The `_e_FStringFormat` empty-spec branch was also updated for
consistency (Proposal No.1) so future parser changes that re-enable
the `_FStringFormat` path for empty-spec cases will continue to
honor `__format__`.
