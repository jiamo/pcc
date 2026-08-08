# PERF-P2-PASS-WIRE focused evidence — 2026-08-14

Mode: host-source pipeline orchestration tests; no stage2 performance or
bootstrap claim.

Command:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_py_frontend_ir_pass_pipeline.py -k 'self_backend_native_compile_defaults_to_bounded_python_ir_pass_manifest or self_backend_emit_llvm_defaults_to_bounded_python_ir_pass_manifest or explicit_python_ir_pass_env_overrides_self_backend_default or self_backend_explicit_default_parent_transport_policy_selects_memory'
```

Result: `4 passed, 81 deselected in 0.83s`.  Native self emission and self
emit-LLVM select the bounded default manifest; explicit environment selection
remains authoritative and the default transport policy remains memory-based.

Open: passes-on/off runtime parity, source-current stage2 wall/RSS measurement,
and bootstrap baseline.
