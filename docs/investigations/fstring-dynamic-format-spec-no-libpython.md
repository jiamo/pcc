# Investigation: dynamic f-string format spec f"{v:>{w}}" (no-libpython)

## Status
resolved 2026-05-30 (fix #61) for the bare-identifier nested-field case
(dynamic width / precision). Complex nested-expr fields remain on the static
path (would need a parser-level fix); tracked under "Remaining" below.

## Problem Description
In strict no-libpython mode, an f-string whose format spec contains a nested
replacement field — `f"{v:>{w}}"` (dynamic width), `f"{v:.{p}f}"` (dynamic
precision), `f"{v:0{w}x}"` — raised `ValueError: unsupported format specifier`
at runtime. These are very common in table / report formatting.

Found 2026-05-30 by the real30 advanced-idiom batch probe.

## Repro
```bash
printf 'def main():\n    w=6\n    print(f"[{42:>{w}}]")\nmain()\n' > /tmp/d.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/d.py -o /tmp/d_bin
/tmp/d_bin            # ValueError: unsupported format specifier
python3 /tmp/d.py     # [    42]
```

## Root cause
`py_parse.py::_parse_fstring_expr` extracts the format spec as a flat raw
substring after `:` (`format_spec = text[i+1:]`), so `{42:>{w}}` yields the spec
string `">{w}"`. `py_lift.py::_e_FStringFormat` then emits `format(v, ">{w}")` —
the nested `{w}` is never evaluated, and `">{w}"` is not a valid spec → the
runtime `py_obj_format` raises "unsupported format specifier".

## Test [CONFIRMED]
`tests/python/test_native_fstring_dynamic_spec.py` (dynamic width >/</^, dynamic
precision, dynamic width+hex, static-spec regressions). Observed the ValueError
before the fix.

## Proposals
- No.1 LIFT: assemble the spec at runtime for bare-identifier nested fields  [CONFIRMED #61]
- No.2 PARSER: parse the spec as a nested f-string (handles arbitrary exprs)   [deferred]

## No.1 LIFT-level runtime spec assembly [CONFIRMED #61]
### Code Change (py_lift.py)
- `_fstring_spec_to_expr(spec_text, span)`: a static spec (no `{`/`}`) returns
  the StrLit unchanged (fast path — zero regression for the common static case).
  A spec with a nested **bare-identifier** field becomes a runtime
  concatenation: `">{w}"` → `BinOp('+', StrLit(">"), Call(str, [Name("w")]))`;
  `".{p}f"` → `"." + str(p) + "f"`. `{{`/`}}` are literal braces. A field that
  is not a bare identifier returns None → caller keeps the static spec.
- `_spec_field_is_ident` + a manual `}` scan keep this self-host-safe (no
  `str.isidentifier`, no 2-arg `str.find`).
- `_e_FStringFormat` calls it for the spec arg; the assembled string is handed
  to `format()` → `py_obj_format` parses it at runtime.
### CONFIRMED
`/tmp/gap_probe/dynspec.py` IDENTICAL to python3 (dynamic width/align/precision/
hex + static regressions). Test 2 passed. FULL three-stage bootstrap 18 passed /
4 skipped (py_lift.py is bootstrap-critical and stays green).

## Remaining (No.2, deferred)
Complex nested-expr fields — `f"{v:>{a+b}}"`, `f"{v:{obj.w}}"`, `f"{v:{f(x)}}"`
— keep the static spec today (still error). The clean fix is parser-level: in
`_parse_fstring_expr`, when the spec contains `{`, parse it as a nested f-string
(it is structurally one) via a body-level variant of `_parse_fstring_parts`
(the existing splitter takes a quoted token, so it needs a body-accepting form),
storing a structured spec the lift turns into the same runtime concatenation but
over arbitrary lifted exprs. Lower priority — bare-identifier fields are the
overwhelmingly common case and are now handled.
