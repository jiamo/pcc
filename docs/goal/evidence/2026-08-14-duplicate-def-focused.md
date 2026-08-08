# Duplicate function-definition focused evidence (2026-08-14)

Mode: host-pcc LLVM emit-only and source host-contract inspection. No runtime
archive or pcc1 was used.

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_duplicate_function_definition.py::test_duplicate_function_definitions_emit_distinct_verified_bodies \
  tests/python/test_duplicate_function_definition.py::test_duplicate_definition_codegen_state_is_in_closed_world_host_contract
2 passed in 0.66s
```

Two same-named definitions now produce separately verified native bodies,
adapters and function-value caches, and the state needed to preserve their AST
identity is part of the closed-world L1 host contract. LLVM/self executable
rebinding, control-flow definitions, the host-vs-pcc1 differential and the
sequential bootstrap remain open.
