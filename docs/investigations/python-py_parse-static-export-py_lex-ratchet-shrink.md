# Investigation: `pcc.parse.py_parse` 10→0 off-mode fallback via `py_lex` static export

## Status
resolved

## Problem Description

`pcc.parse.py_parse` standalone per-module compile had 10 residual
`py_cpy_*` calls under `ir_scaffold_mode=on` (and the same shape in
off-mode). Histogram:

```
3x @py_cpy_getattr   (cpy.mod.py_lex.Lexer, .tokenize, ...)
2x @py_cpy_from_pccstr
2x @py_cpy_call
1x @py_cpy_decref
1x @py_cpy_ensure_init
1x @py_cpy_import    (pcc.parse.py_lex)
```

All 10 come from the single call shape

```python
from . import py_lex as pl
...
return pl.Lexer(src, filename).tokenize()
```

`pcc.parse.py_lex` had no entry in
`pcc/py_frontend/codegen/layer1_support.py::_PCC_FRONTEND_STATIC_NATIVE_EXPORTS`,
so the standalone per-module compile of `py_parse.py` couldn't bind
`py_lex.Lexer` / `Lexer.tokenize` to a native pcc class — it fell
through to `py_cpy_import` + `py_cpy_getattr` + `py_cpy_call`.

## Repro

```bash
env -u LC_ALL uv run python -c "
import sys; sys.path.insert(0, '.')
from pcc.py_frontend.codegen import layer1
from pcc.py_frontend import type_infer
from pcc.parse.py_lift import parse_and_lift
src = 'pcc/parse/py_parse.py'
ast_mod = parse_and_lift(open(src).read(), src, 'pcc.parse.py_parse')
typed = type_infer.infer_module(ast_mod)
cg = layer1.L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
import re
print(len(re.findall(r'\bcall [^\n]*@py_cpy_[a-z_]+', str(cg.generate(typed)))))
"
```

Pre-fix prints `10`; post-fix prints `0`.

## Test [CONFIRMED]

`tests/python/test_fallback_baseline.py` + `_ir_py_fallback_baseline.py`
+ `_bootstrap_gate_baseline.py` all pass after baseline recapture.
Full 3-stage self bootstrap passes in 61.8s.

## Proposals

- No.1 Add static export entries for `pcc.parse.py_lex`             [CONFIRMED]

## No.1 py_lex static export
### Code Change

`pcc/py_frontend/codegen/layer1_support.py`:

1. New `_PCC_FRONTEND_STATIC_NATIVE_EXPORTS["pcc.parse.py_lex"]`
   entry with three classes:
   - `Token` (frozen dataclass — `kind`, `text`, `line`, `col`).
   - `LexError` (Exception subclass — empty schema).
   - `Lexer` with the 10 instance fields (`src`, `_src_len`,
     `_debug_bootstrap`, `filename`, `pos`, `line`, `col`,
     `_indent_stack`, `_paren_depth`, `_at_line_start`) and the
     two methods py_parse actually calls
     (`__init__(self, src, filename="...")` and
     `tokenize(self)`).

2. `pcc.parse.py_lex` added to `_PCC_FRONTEND_STATIC_NATIVE_MODULES`
   (frozenset).

3. `pcc.parse.py_lex` added to `_default_native_module_exports`'s
   inline allowlist check.

`tests/fallback_baseline.json`:
- `on_mode_per_module["pcc.parse.py_parse"]`: 10 → 0.
- `per_module["pcc.parse.py_parse"]`: 10 → 0.
- Added a recapture log entry
  (`2026-05-28-py_lex-static-export`) explaining the cause + fix.

### CONFIRMED
- `pcc/parse/py_parse.py` standalone compile: 10 → 0 `py_cpy_*` calls.
- Fallback baselines pass (17 passed / 4 skipped).
- 3-stage self bootstrap green (1 passed in 61.8s).
- The closed-world multi-file `on_mode_totals["fallbacks_total_multi"]`
  stays at 0 (unchanged) — this slice is a standalone-per-module
  diagnostic improvement, not a closed-world total change.

### Why this is the right shape
`_PCC_FRONTEND_STATIC_NATIVE_EXPORTS` is the existing static
schema-export mechanism for letting standalone per-module
compiles bind cross-module class / function references natively.
`py_lex` was an oversight in the previous py_parse / py_lift
export landings (the dependency is one-step removed: py_parse
imports py_lex, but most prior work focused on the
py_lift/py_parse pair).

The class method descriptors follow the same dict-literal shape as
the existing `L1CodeGen.__init__` / `generate` entries: `name`,
`kind="instance"`, `return_ty`, `param_types`, `call_sig`,
`box_int_abi=False`.

## Report
Landed. This is a B-P0-PKG fallback-shrink slice — the selected
task card calls for "the next generic fallback shrink through
``--python-libpython=auto`` diagnostics without weakening the
strict no-libpython ABI gate." The closed-world multi-file
ratchet stays at 0; the standalone per-module diagnostic now
locks in py_parse at 0 too.

A follow-up sweep could look at other modules that import py_lex
(none today inside the closure, but new harness modules might).
