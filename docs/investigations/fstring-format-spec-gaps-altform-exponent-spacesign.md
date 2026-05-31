# Investigation: f-string / format() spec gaps — alt-form `#x`, exponent `e`/`E`, space-sign ` `

## Status
resolved (#30 alt-form, #31 int space-sign format(), #32 f-string spec literal completing space-sign; e/E/g already worked — all full-bootstrap-passed)

## Problem Description
Under strict no-libpython (`--backend self --python-libpython=off`, DEFAULT
ports), several standard format-spec mini-language features raise
`ValueError: unsupported format specifier` (from py_format.c at runtime) or
render wrong:

| spec | example | pcc | CPython |
|---|---|---|---|
| alt-form hex | `f"{42:#06x}"` | ValueError | `0x002a` |
| exponent | `f"{42:e}"` | ValueError | `4.200000e+01` |
| space-sign | `f"{3.14: .2f}"` | `3.14` (no lead space) | ` 3.14` |

Verified working (same probe): `+.2f`, `>8.3f`, `b`, `08b`, `+d`, `,`,
`1234567:,`, `o`, plain `.2f`/`x`/`d`. So sign `+`, align/width/precision,
binary, octal, thousands all work; the gaps are the alt-form `#` prefix, the
exponent `e`/`E` presentation type, and the space ` ` sign option.

## Repro
```python
def main():
    print(f"{42:#06x}")    # pcc ValueError; CPython 0x002a
    print(f"{42:e}")       # pcc ValueError; CPython 4.200000e+01
    print(f"{3.14: .2f}")  # pcc "3.14";     CPython " 3.14"
main()
```

## Root cause
- Compile-time `format_lowering._emit_format_spec_builtin`: the `x` handler
  (line ~430) parses `body = spec[:-1]` and only accepts an optional leading
  `0` + digits; a `#` prefix (`#06x`) fails `width_text.isdigit()` and falls
  through. There is no `e`/`E` branch and no space-sign handling. Unhandled
  specs fall to the runtime `py_obj_format` (py_format.c), whose spec parser
  rejects `#`/`e` with "unsupported format specifier".
- `py_int_format_hex` has no alt-form (`0x` prefix) flag.

## Proposals
- No.1 extend the runtime format-spec parser (py_format.c, C-only — both modes,
  no port) to handle: alt-form `#` (prefix `0x`/`0o`/`0b` for x/o/b), the `e`/`E`
  presentation type (scientific, default 6 digits), and the space ` ` sign
  option (leading space for non-negative). Then route the frontend to it (or let
  the existing fallthrough reach it). [pending — the coherent fix]
- No.2 piecemeal compile-time handling in `_emit_format_spec_builtin` (add `#`
  parse to the x branch + py_int_format_hex alt flag; add an `e` branch via a
  new py_float_format_exp). [pending — partial]

## Scope / priority note
The format mini-language is a coherent subsystem; these three features
(alt-form, exponent, space-sign) are best added together with care for combos
(e.g. `#06x` = alt + zero-pad + width). `#x` (0x-prefixed hex) and `e`
(scientific) are moderately common; space-sign is rare. Lower-frequency than the
root-cause classes resolved this session (generator-consumption, print/str via
__str__, boxed-float arithmetic). Found by real8.py via the realistic-program
CPython-diff methodology (which also confirmed closures/nonlocal, starred
unpacking, for/while-else, walrus, chained comparison, dict/set comprehensions
all work). A focused follow-up: extend the py_format.c spec parser, full
bootstrap, regression test.

## Update (2026-05-30): alt-form `#06x` FIXED (#30); `e` was a false alarm; only space-sign remains
- `#06x`/`#010x`/`#06X` (alt + zero-pad) FIXED in py_format.c format_int_builtin (zero-pad after the 0x prefix). Test: test_native_format_spec_altform_hex.py.
- `e`/`E`/`g`/`G` exponent for FLOATS already worked (format_float_builtin); the earlier 'unsupported' was the #06x error masking it. Only `format(<int>, 'e')` (int with a float-presentation spec) is unsupported — niche.
- STILL OPEN (minor, rare): space-sign ` ` — f"{3.14: .2f}" lacks the leading space; f"{42: d}" too. format_float_builtin parses sign_space but may not apply it; format_int_builtin does not parse ` `. Low priority.

## Update (2026-05-30): space-sign precisely scoped (rare, multi-part, deferred)
- FLOAT via builtin format() WORKS: `format(3.14, ' .2f')` -> ` 3.14` (format_float_builtin already applies sign_space, line ~85).
- INT via builtin format() FAILS: `format(42, ' d')` -> ValueError (format_int_builtin does NOT parse a leading ' ' sign option; it parses '+' only).
- F-STRING float DROPS the space: `f"{3.14: .2f}"` -> `3.14` (no leading space) even though `format(3.14,' .2f')` works -> the f-string/`_emit_format_spec_builtin` path for a FloatType value routes ` .2f` somewhere that drops the leading space (different path from the format() builtin).
So space-sign is multi-part (format_int_builtin parse+apply ' '; f-string float spec routing) and RARE. Deferred as low-ROI. The valuable format-spec gap (#06x alt-form) was fixed in #30; `e`/`E`/`g` exponent already work.
