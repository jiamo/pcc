# AUD-P2-SELF-MODULE-SPECIAL-CASES-IN-CODEGEN — the layout contract had already drifted

Mode: host pcc, Python frontend. No bootstrap-critical codegen was changed;
only the two contract tables and a new enforcing test.

## What the row's boundary said, and what is actually there

The boundary read "the two semantic self-compile special cases in
`class_gen.py`". Measured inventory of module-name comparisons under
`pcc/py_frontend/codegen/`:

```text
76 comparisons of the form == "pcc.…" across 17 files
  layer1_support.py 27   native_virtual_thread.py 13   native_modules.py 6
  import_lowering.py 4   generator_lowering.py 4       class_gen.py 4
  call_expression_lowering.py 4   ir_scaffold_lowering.py 3
  type_abi_lowering.py 2  module_global_lowering.py 2
  + 7 files with one each
```

So the row's own gate ("`rg -n 'module.name == \"pcc.'` returns no name-keyed
semantic branches") cannot be met by fixing `class_gen.py` alone. That is a
multi-file, bootstrap-critical refactor, not the two-site cleanup the boundary
described.

## The drift the mechanism was already producing

`PY_AST_FIELD_NAME_OVERRIDES` is hand-maintained in **two** places:
`pcc/py_frontend/py_ast_contract.py` (read by `class_gen.py`) and a private
copy `_PY_AST_FIELD_NAME_OVERRIDES` in `pipeline.py`. They had diverged:

```text
py_ast dataclasses                     66
pinned in py_ast_contract.py           64   (missing SetType, ValueArrayType)
pinned in pipeline.py's copy           65   (missing ValueArrayType)
pinned order != real dataclass order    0
```

`SetType` reached the pipeline copy and never the contract file. Neither knew
about `ValueArrayType`. Those two nodes therefore took the pinned path on one
side and the inferred path on the other — the exact class-layout drift the
contract exists to prevent. The pinned *values* had not yet diverged, so this
was a live hazard rather than a live miscompile.

## Change

Both entries added to both copies, derived from the real dataclass field
order (`SetType` -> `("name", "elem")`, `ValueArrayType` ->
`("name", "elem", "length")`, matching what inference produced).

`tests/python/test_py_ast_field_contract.py` now enforces what nothing did:

- the two copies must be identical
- every pinned order must equal the real dataclass field order
- every `py_ast` dataclass must be pinned

That converts the hand-maintenance hazard into a ratchet without touching the
name-keyed branches, which stay open work.

## Evidence

```text
tests/python/test_py_ast_field_contract.py                            3 passed
tests/python/test_py_multi_file_compile.py
  test_py_multi_file_bootstrap_shim.py
  test_fallback_baseline.py test_ir_py_fallback_baseline.py   160 passed (11:46)
```

The frontend gate matters here specifically because it includes the fallback
ratchet: adding two entries to the override table changes which path
`SetType` and `ValueArrayType` take through `class_gen`, and a shift in
either class layout or the per-module fallback counts would show up there. It
did not.

The full self-host bootstrap gate could not run here: it deselects because the
stage1/2/3 binaries are absent (`tests/python/test_bootstrap_gate_baseline.py`
-> 4 deselected), and populating them needs a `scripts/bootstrap.sh` run that
was not authorized in this session. Stated rather than papered over.
