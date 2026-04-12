# P6C.5 de-PLY Spike Report

**Date:** 2026-04-21
**Status:** complete (timebox: 3-5 days allocated, actual ~half day)
**Recommendation:** Route **B'** (table-driven driver + PLY at build time)

## Goal

Decide how to replace PLY at the self-host compile path, given:
- User constraint: PLY code stays in source tree (`PCC_USE_PLY_C_PARSER=1` reverse-opt-out forever)
- Strategy C goal: self-host binary must NOT link PLY
- 146 grammar rules to preserve

## Two routes evaluated

### Route A1 — Hand-written recursive-descent C parser

- Like `pcc/parse/py_parse.py` (already landed, 1500 LoC, 105/105 parity)
- Rewrite all 146 LR grammar rules as recursive-descent
- 3-5K LoC new code
- Grammar work touches tricky parts of C: declarator nesting, typedef-name
  context-sensitivity, C11/C23 additions

### Route B' — Table-driven LR driver + PLY build-time

- PLY generates ACTION/GOTO/production tables at **build time** only
- Frozen as Python literals in `pcc/parse/c_parsetab.py` (~267 KB)
- Write a ~300 LoC shift/reduce driver in pcc-compilable Python
- Extract each `p_*` method's body into a module-level function keyed
  by production index
- **Zero PLY imports at runtime** in the self-host binary

## Data findings (from live probe)

```
Grammar rules:            146 (p_* methods in c_parser.py)
LR method:                LALR
ACTION states:            561
GOTO states:              175
Productions:              308 (incl. augmented + opt-rules)
ACTION entries:           13,599
GOTO entries:             1,878
Serialized table size:    267 KB JSON
```

Rule complexity distribution:
- 24 trivial (1-line body, e.g. `p[0] = p[1]`)
- 59 simple (2-5 lines)
- 41 medium (6-10 lines)
- 22 complex (10+ lines, biggest 42)

Total action-body LoC: ≈1,200 (existing, just needs re-wiring).

## Comparison

| Metric | A1 hand-written RD | B' table-driven |
|---|---|---|
| New code | 3-5K LoC | ~500 LoC driver + table dump |
| Grammar rewrite | YES (146 rules from scratch) | NO (reuse existing bodies) |
| Self-host runtime PLY dep | zero | zero (table is static data) |
| Build-time PLY dep | zero | PLY runs once, dumps tables |
| Risk of semantic divergence | HIGH (rewriting LR→RD is subtle) | LOW (same action bodies) |
| typedef-name context handling | requires manual lookahead tricks | PLY already handles via lexer hack |
| C11/C23 extensions | explicit work | already in grammar |
| Time estimate | 3-4 weeks | 3-5 days |
| Long-term maintainability | better conceptually | grammar edit ⇒ regenerate table |

## Key insight

**B' keeps PLY as a build-time code generator, not a runtime library.**
The 267 KB LR table is pure static data — same shape as what bison
emits into generated `.c` files for GCC. Our self-host binary never
imports PLY; it just reads a frozen dict at startup.

This pattern matches how every mature LALR-based C/C++ compiler ships
(GCC/Clang use hand-written parsers, but older ones used bison-generated
tables — same principle).

## Critical scope note — lexer side is ALSO PLY

Current `pcc/lex/c_lexer.py` imports `pcc/ply/lex.py`:
```
from ..ply import lex
from ..ply.lex import TOKEN
```

So "de-PLY" must cover **both** yacc (parser runtime) and lex (tokenizer
runtime). If we only replace the yacc half, the self-host default path
still imports PLY — Strategy C's "zero PLY runtime" is not reached.

The epic therefore breaks into three checkpoints, each is a separate
merge-gate. The umbrella is M3 path α; the sub-stages are α1/α2/α3.

## Recommended implementation (M3 path α)

### α1 — Parser-side native LR runtime

Replace PLY `yacc` runtime with a frozen-table driver. No PLY yacc
import at runtime on the default path.

Sub-steps:
1. `scripts/freeze_c_parser_tables.py` — run PLY once (build-time only),
   write `pcc/parse/c_parsetab.py`:
   - `ACTION: dict[int, dict[str, int]]`
   - `GOTO: dict[int, dict[str, int]]`
   - `PRODUCTIONS: tuple[tuple[str, int, int], ...]` — (lhs, len, action_id)
2. `pcc/parse/c_parser_actions.py` — extract each `p_*` method body
   into a module-level `action_<N>(stack)` function. No PLY-specific
   surface (`p[0] = ...` → plain list indexing).
3. `pcc/parse/c_parse_driver.py` — shift/reduce state machine (~300
   LoC). Consumes tokens from **existing `CLexer`** (still PLY-lex
   for now — closed in α2).
4. Gate: oracle harness (#141) 63/63 green with `PCC_NATIVE_C_PARSER=1`.

Exit: parser side does not import `pcc.ply.yacc` on default path.
Lexer side is still PLY — α2 closes that.

### α2 — Lexer-side native

Replace `pcc/lex/c_lexer.py` with a hand-written C tokenizer that does
not import `pcc.ply.lex`. Same interface the `CParseDriver` expects
(produce token stream with `ID` / `TYPEID` disambiguation via
parser-provided symbol-table callback).

Style: like `pcc/parse/py_lex.py` (hand-written, list-producing, no
yield). Estimated ~500 LoC.

Gate:
- Token stream parity: every test in oracle corpus produces identical
  token sequences vs. old CLexer.
- `grep -r 'pcc.ply' pcc/lex/ pcc/parse/` on the default path returns
  nothing (other than the `PCC_USE_PLY_C_PARSER=1` fallback loader).

### α3 — Flip default + real-project validation + fuzz

- `PCC_NATIVE_C_PARSER=1` becomes the default; opt-out is
  `PCC_USE_PLY_C_PARSER=1`.
- Real-project validation on `tests/nginx`, `tests/openssl`,
  `tests/sqlite`, `tests/postgresql` — compile with both parsers,
  diff the IR byte-for-byte.
- csmith fuzz: 10k random valid C, zero diff.

## Exit criteria (hardened per Codex review)

- ✅ Default self-host C parser path **does not import `pcc/ply/*`**
  (neither lex nor yacc runtime)
- ✅ Parser driver has no PLY yacc runtime
- ✅ Lexer has no PLY lex runtime
- ✅ `PCC_USE_PLY_C_PARSER=1` opt-out fallback preserved
- ✅ Oracle parity 63/63 maintained
- ✅ Real project IR byte-identical under both parsers
- ✅ csmith fuzz zero diff for N days

## Total estimated effort

- **α1** (parser driver): ~3 days
- **α2** (lexer): ~3-4 days
- **α3** (flip + validation): ~2 days

**M3 path α total: ~1.5 weeks**, vs. 3-4 weeks for A1.

## Exit condition met

- ✅ Token stream interface: existing `CLexer` stays unchanged, driver
  consumes it as-is
- ✅ Action/grammar mechanical migration: 146 rules × ~8 lines avg,
  straightforward rewrite from `p[0] = p[1]` → `return stack[-1]`
- ✅ Driver complexity: ~300 LoC shift/reduce is well-understood

## Open questions (non-blocking)

1. **Lexer lookahead for typedef-name**: PLY's lexer coordinates with
   the parser via the symbol table (`ID` vs `TYPEID` disambiguation).
   Our driver needs to expose the current symbol-table snapshot to the
   lexer each reduce. → Non-trivial but well-contained; 1 day work.

2. **Error recovery**: PLY has `p_error` handler. Driver needs same
   surface. → Simple pattern, copy semantics from PLY.

3. **Table regeneration trigger**: grammar edits require running the
   dumper. → `Makefile` rule, CI check that `c_parsetab.py` hash
   matches grammar source.

## Recommendation

**Proceed with B'. Unblock #136 → C parser replacement.**

If B' hits unexpected complexity during Step 3 (driver), fall back
to A1 — oracle harness (#141) remains valid target either way.
