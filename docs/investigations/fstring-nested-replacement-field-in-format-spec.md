# Investigation: nested replacement field inside an f-string format spec (f"{val:>{width}.2f}") unsupported under no-libpython

## Status
active

## Problem Description
A nested replacement field inside an f-string's format spec —
`f"{val:>{width}.2f}"`, `f"{x:{fill}>{w}}"`, `f"{n:.{prec}f}"` — raises at runtime:

```
ValueError: unsupported format specifier
```

CPython evaluates the nested `{width}` / `{prec}` field first (it is itself a
mini replacement field), producing a concrete spec like `>8.2f`, then applies
it. pcc passes the spec **literally** (`>{width}.2f`, with the `{width}` text
unevaluated) to `py_obj_format`, which cannot parse it.

Found 2026-05-30 by real16 (`f"{val:>{width}.2f}"`). Common idiom: dynamic-width
/ dynamic-precision alignment in report/table formatting.

## Repro
```bash
cat > /tmp/fs.py <<'PY'
def main():
    width = 8
    val = 3.14159
    print(f"{val:>{width}.2f}")   # CPython: "    3.14"
    print(f"{42:#0{width}x}")     # CPython: "0x00002a"
    print(f"{3.14159:.{2}f}")     # CPython: "3.14"
main()
PY
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/fs.py -o /tmp/fs_bin
/tmp/fs_bin            # ValueError: unsupported format specifier
python3 /tmp/fs.py     # "    3.14" / "0x00002a" / "3.14"
```

## Root cause (CONFIRMED by reading)
- `pcc/parse/py_parse.py` `_parse_fstring_expr` (~line 2002) extracts the spec as
  raw text: `format_spec = text[i + 1:]`. The nested `{...}` is left in the string.
- `_FStringFormat(expr, conversion, spec, line)` stores `spec` as that raw string
  (`pcc/py_frontend/codegen/layer1_support.py:523` declares the 4-tuple shape).
- `pcc/parse/py_lift.py` `_e_FStringFormat` (~line 725) lowers a non-empty spec to
  `format(inner, StrLit(spec_text))` — a literal-string spec arg. The nested field
  is never evaluated.
- `py_format.c` then rejects `>{width}.2f` → "unsupported format specifier"
  (py_format.c:548/808/833).

So the error is in the **runtime**, but the **bug** is the frontend handing it an
unevaluated spec.

## Proposals
- No.1 Parse nested spec fields into a structured spec, lift as a dynamic spec expr   [pending]

## No.1 Parse nested spec fields, build a dynamic spec expression
### Sketch
The parser already has `_parse_fstring_parts(raw, line)` which splits an f-string
body into `[_FStringText | exprnode]`. A spec like `>{width}.2f` is exactly a tiny
f-string body. Two viable shapes:

(a) **Parse-time, structured spec.** When the extracted `format_spec` contains an
unescaped `{`, parse it (the inner loop of `_parse_fstring_parts`, which needs the
quote/prefix stripping factored out) into parts and store them on `_FStringFormat`.
At lift, if the spec is structured, build `spec_expr` = concat of `_FStringText`
literals + `format(field, "")` for each field, then lower to `format(inner,
spec_expr)`. Reuses the existing spec-expr path (`_emit_obj_format_call` already
accepts a spec *expression*).

(b) **Reuse `spec` as a union (str | _FString node).** Parser sets `spec` to a
pre-parsed nested-f-string node when it contains `{`; lift dispatches on type.
Avoids a new field but makes `spec` polymorphic.

### Bootstrap-sensitivity (why this is NOT a quick slice)
Both shapes touch `pcc/parse/py_parse.py` (parser), `pcc/parse/py_lift.py` (lift),
and the self-host node-shape shim (`layer1_support.py:523`). Per AGENTS.md, changes
to `py_parse.py` / `py_lift.py` are bootstrap-critical and require the full
stage1→stage2→stage3 self-host to be proven green, plus the PLY/self-host node shim
must agree on the (possibly new) `_FStringFormat` field. Shape (a) with an explicit
new field (e.g. `spec_parts`, default None for the common no-nested-field case) is
the lower-risk option because the simple-spec path is unchanged. Do this as a
dedicated task with the bootstrap gate, not bundled with an unrelated fix.

### pending
Not yet implemented. Deferred from the 2026-05-30 div-by-zero (#37) session as a
distinct, bootstrap-sensitive feature task. Predecessor f-string work:
fstring-format-spec-gaps-altform-exponent-spacesign.md (the *literal* spec
mini-language: alt-form, exponent, space-sign — all resolved). This gap is the
*dynamic* (nested-field) spec, which that work did not cover.
