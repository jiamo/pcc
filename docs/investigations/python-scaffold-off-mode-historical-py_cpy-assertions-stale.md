# Investigation: scaffold tests assert OFF-mode `py_cpy_*` behavior that pcc closed

## Status
resolved

## Problem Description

Four tests in the scaffold reduction / load / store family failed
asserting *historical* OFF-mode behavior that pcc has improved past:

- `tests/python/test_ir_scaffold_reduction.py::test_layer1_shaped_on_reduces_fallbacks`
  — `assert n_on < n_off` with `off=0 on=0`.
- `tests/python/test_ir_scaffold_reduction.py::test_layer1_shaped_off_still_uses_py_cpy_for_those_patterns`
  — `assert n_off > 0` with `n_off=0`.
- `tests/python/test_ir_scaffold_load.py::test_off_mode_still_uses_py_cpy_for_load`
  — `assert "py_cpy_" in body` with no `py_cpy_*` in the OFF-mode IR.
- `tests/python/test_ir_scaffold_store.py::test_off_mode_still_uses_py_cpy_for_store_callsite`
  — same shape.

All four tests are from the Phase 5 scaffold-introduction era when
`builder.<op>` and `self.runtime[<name>]` patterns went through
libpython fallback in OFF mode. pcc has since closed the OFF-side
gap (native runtime + typed-int dispatch), so the synthetic source
no longer forces `py_cpy_*` in either mode. The behavior change is
the desired direction; the test polarity hadn't been updated.

A separate stale-import issue in
`tests/python/test_ir_scaffold_load.py::test_load_arity_check`:

```
ImportError: cannot import name 'ScaffoldUnsupportedError' from
'pcc.py_frontend.codegen.layer1'
```

`ScaffoldUnsupportedError` moved from `pcc.py_frontend.codegen.layer1`
to `pcc.py_frontend.codegen.ir_scaffold_lowering` in the layer1
split; the test still used the old import path.

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_ir_scaffold_load.py \
  tests/python/test_ir_scaffold_store.py \
  tests/python/test_ir_scaffold_reduction.py \
  -q -n0
```

Pre-fix: 5 failures (4 historical-behavior asserts + 1 stale import).

## Test [CONFIRMED]

Same pytest run; pre-fix 5 fail / 6 pass, post-fix 11 / 11 pass.

## Proposals

- No.1 Flip the polarity to "OFF must not regress past ON"  [CONFIRMED]
- No.2 Update stale ScaffoldUnsupportedError import path     [CONFIRMED]

## No.1 OFF-must-not-regress-past-ON polarity
### Code Change
Each "OFF still uses py_cpy_*" test was updated to:

```python
on_text = _compile_to_ll(<same source>, mode="on")
on_body = _function_body(on_text, "<fn>")
off_cpy = body.count("py_cpy_")
on_cpy = on_body.count("py_cpy_")
assert off_cpy <= on_cpy, ...
```

so OFF mode is only allowed to *match or beat* ON mode for the
specific scaffolded idiom, not to introduce new libpython fallback.
The "ON reduces fallbacks" test was rewritten to assert
`n_on == 0 and n_on <= n_off`.

### CONFIRMED
- `test_ir_scaffold_load.py` 4 / 4 (was 2 failures);
  `test_ir_scaffold_store.py` 4 / 4; `test_ir_scaffold_reduction.py`
  3 / 3 (was 2 failures).
- All 267 `test_ir_scaffold_*.py` + `test_format_protocol.py`
  pass.
- Fallback baselines unchanged: 17 passed, 4 skipped.

### Why this is correct
Asserting "OFF must keep py_cpy_*" was a Phase 5 design checkpoint
that locked the *boundary* between the new scaffold dispatch and the
old libpython fallback. With the boundary now fully closed (typed-int
+ native runtime dispatch land in OFF too), the historical assertion
became inverted. The remaining durable invariant — "OFF mode is never
worse than ON for these scaffolded idioms" — is what the tests should
actually defend.

## No.2 ScaffoldUnsupportedError import path
### Code Change
```python
from pcc.py_frontend.codegen.ir_scaffold_lowering import (
    ScaffoldUnsupportedError,
)
```
(was `from pcc.py_frontend.codegen.layer1 import
ScaffoldUnsupportedError`).

### CONFIRMED
`test_load_arity_check` now passes.

## Report
Five tests sweep + one stale import fix, no production code changes.
The "scaffold ON reduces fallback" gate is now a regression guard
("OFF must never be worse than ON") rather than a checkpoint of an
intermediate transition state.
