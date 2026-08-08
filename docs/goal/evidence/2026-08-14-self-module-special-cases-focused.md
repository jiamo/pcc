# AUD-P2-SELF-MODULE-SPECIAL-CASES-IN-CODEGEN focused evidence — 2026-08-14

Mode: host-source contract and codegen metadata tests; no bootstrap claim.

Commands/results:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_py_ast_field_contract.py
# 3 passed in 0.07s

gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_self_module_contracts.py
# 4 passed in 0.08s

rg -n 'module\.name == "pcc\.' pcc/py_frontend/codegen/
# no matches
```

The declarative module-capability registry and AST field contract are green,
and the codegen tree has no direct source-module `pcc.*` equality branch.

Open: fallback ratchets and the final source-current sequential bootstrap.
