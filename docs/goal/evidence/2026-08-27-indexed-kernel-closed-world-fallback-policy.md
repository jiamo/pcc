# Indexed kernel closed-world fallback policy — 2026-08-27

Claim level: focused fallback-probe correctness for the compiler native data
plane. This does not yet prove the complete fallback suite, a rebuilt pcc1,
Stage2/Stage3, fixed point, or GC0..4 transfer.

The accepted Indexed Function Kernel introduced typed sibling calls across
self-backend Modules. Raw standalone inference lacks those sibling exports and
reported new `py_cpy_*` actions, although the production multi-file closure
remained zero-fallback. Direct substitution through the existing closed-world
context produced ON-mode zero for all nine affected Modules; representative
raw actions were 77 for `self_backend_kernel` and 465 for
`self_backend_precise_stackmaps`.

The fix adds a distinct `closed-world` probe policy for
`pcc.backend.self_backend_*`. It supplies sibling schemas without pretending
the Module is an `L1CodeGen` mixin. Raw counts remain observable but are not
the semantic gate; the contextual ON result remains hard zero. No fallback
baseline value was raised or added.

Focused results:

```text
gtimeout 30s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_fallback_baseline.py::test_self_backend_data_plane_uses_closed_world_probe_policy \
  tests/python/test_fallback_baseline.py::test_pipeline_and_codegen_host_contract_do_not_drift \
  tests/python/test_hoist_module_boundaries.py
6 passed in 0.43s

gtimeout 120s ... pytest -vv -x --tb=short -n0 \
  tests/python/test_fallback_baseline.py::test_self_backend_native_data_plane_closed_world_fallback_zero
1 passed in 15.12s

gtimeout 360s ... pytest -vv -x --tb=short -n0 \
  tests/python/test_fallback_baseline.py::test_per_module_fallbacks_under_ratchet
1 passed in 271.51s
```

The first attempted 60-second run of the nine-Module gate produced no final
pytest summary and is not counted as evidence. The successful rerun is retained
at
`build/indexed-packed-record-stage1-candidate-v1/gates/fallback-closed-world-policy-focused.log`;
the original ratchet log is
`build/indexed-packed-record-stage1-candidate-v1/gates/fallback-per-module-ratchet.log`.

Open boundary: run the complete fallback files after the shared performance
machine is idle, run strict closure for the changed compiler Modules, then
rebuild the now-invalidated source-frozen candidate and repeat the item311
worker gate before any Stage2/Stage3 transfer.
