# Investigation: `yield a, b` misparses as `(yield a), b` and leaks the `_yield` sentinel

## Status
resolved (parser fix landed 2026-05-27; regression test in
`tests/python/test_python_generator_parity.py::test_generator_yields_implicit_tuple`)

## Problem Description

A generator that yields an implicit tuple, e.g. `yield 1, 2` or
`yield rpath, files`, compiles without diagnostics but crashes at runtime with:

```
NameError: name '_yield' is not defined
```

This shape is used by real generators in the package/import surface, e.g.
NumPy `numpy/distutils/misc_util.py::general_source_directories_files`
(`yield rpath, files`). Found while reducing toward the
`B-P0-PKG` real-NumPy import path; this is a distinct, backend-independent
frontend bug from the self-backend `BackendUnavailable: ... .owned.N`
generator-emission cap (that one is still open).

## Repro

```python
# /tmp repro, minimal
def gen():
    yield 1, 2

def main():
    for x in gen():
        print(x)

main()
```

```bash
env -u LC_ALL uv run pcc --backend self  --python-libpython=off --ir-scaffold=on gen.py -o /tmp/g && /tmp/g
env -u LC_ALL uv run pcc --backend llvm  --python-libpython=off --ir-scaffold=on gen.py -o /tmp/g && /tmp/g
```

Both backends: `NameError: name '_yield' is not defined`. CPython prints
`(1, 2)`. Backend-independent → frontend bug.

## Root cause (confirmed from emitted IR)

`yield a, b` in CPython means `yield (a, b)` — `yield` is greedy over the
testlist. pcc's hand-written Python parser did not do this:
`pcc/parse/py_parse.py::_parse_yield_expr` parsed the yield value with a
single `self._parse_expr()`, so `yield 1, 2` produced `_Yield(value=1)` and
left `, 2` for the enclosing testlist, yielding the AST `(_yield(1), 2)`.

Consequences:
- `_funcdef_has_yield_sentinel` still found the `_yield(1)` call nested in the
  tuple, so the function *was* detected as a generator (resume function
  `@..._gen_resume` is emitted) — the bug is **not** a missed generator.
- But the statement is an `ExprStmt(TupleExpr(_yield(1), 2))`, not an
  `ExprStmt(_yield(...))`. The generator yield-conversion at
  `generator_lowering.py:227` only converts an ExprStmt whose expr is
  *directly* a `_yield(...)` sentinel. The tuple-wrapped `_yield(1)` was
  therefore lowered as an ordinary expression: a name load of `_yield`
  followed by a call → `NameError` at runtime.

Emitted IR (LLVM backend) confirmed the leak: the resume function built a
2-element `py_tuple_new(2)` whose element 0 was the leaked `_yield` call
(`%yield.dyn.call`) and element 1 was the literal `2`, plus a
`@.name_error` constant `"name '_yield' is not defined"`.

## Test [CONFIRMED]

`tests/python/test_python_generator_parity.py::test_generator_yields_implicit_tuple`
compiles `yield 1, 2` and `for a, b in items: yield a, b` and asserts output
`["(1, 2)", "(3, 4)", "(5, 6)", "(7, 8)"]`. Observed failing before the fix
(NameError), passing after.

## Proposals
- No.1 Make `_parse_yield_expr` consume the testlist into an implicit tuple   [CONFIRMED]

## No.1 Make `_parse_yield_expr` consume the testlist into an implicit tuple
### Code Change
`pcc/parse/py_parse.py::_parse_yield_expr`, mirroring the existing implicit
tuple handling in `_parse_return` (which already does this for
`return a, b`), but with expression-position-safe terminators (`yield` can
appear in expression position, not only as a statement):

```python
val = self._parse_expr()
if self._peek().kind == TK_OP and self._peek().text == ",":
    elems = [val]
    while self._accept(TK_OP, ","):
        nt = self._peek()
        if nt.kind == TK_NEWLINE or (
            nt.kind == TK_OP and nt.text in (")", "]", "}", ":", "=")
        ):
            break
        elems.append(self._parse_expr())
    val = _Tuple(elems=elems, line=kw.line)
return _Yield(value=val, is_from=False, line=kw.line)
```

`yield from` is intentionally left single-expression (PEP 380 takes one
iterable). The Python parser is hand-written recursive descent, not PLY, so
no parser-cache version bump is required.

### CONFIRMED
- Minimal repros (`yield 1, 2`; tuple-unpack-for + `yield a, b`) now print the
  tuple under both `--backend self` and `--backend llvm`, matching CPython.
- `tests/python/test_python_generator_parity.py` -> 6 passed in 9.46s
  (5 prior + the new tuple-yield regression).
- Mandatory self-host gate for `py_parse.py` changes:
  `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  -> 1 passed in 40.20s (stage1 -> stage2 -> stage3 self-backed).

## Report

Landed No.1. The fix is generic (any `yield <testlist>`), not a special case.
It is a prerequisite for compiling real generators such as NumPy's
`general_source_directories_files`, though that function additionally hits a
separate, still-open self-backend `BackendUnavailable: ... .owned.N`
generator-emission gap (track separately).

Note on the no-libpython fallback baseline: a concurrent (now stopped) agent
left uncommitted edits to `pcc/py_runtime/py/py_class.py` and
`pcc/py_frontend/codegen/call_expression_lowering.py` in the shared worktree,
which regress the per-module ratchet for `class_gen` / `pipeline` /
`cli_bootstrap`. Those modules contain no `yield`, so this parser fix cannot
be their cause; the regression is attributable to the unrelated uncommitted
edits, not to this change.
