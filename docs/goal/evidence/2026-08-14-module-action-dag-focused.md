# Per-module action DAG focused evidence

Mode: host-Python unit/contract tests only. This is not pcc1, fixed-point, or
measured speedup evidence.

Command:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_module_action_dag.py \
  tests/python/test_pipeline_frontend_worker_owners.py
```

Result: `16 passed in 0.18s`.

The focused contracts prove canonical graph/action identities, exact no-op
reuse with zero export/codegen workers, one-module private-edit scheduling,
public-summary reverse-closure invalidation, full compiler/runtime identity
invalidation, manifest-last artifact publication, and corrupt/tampered cache
fallback. The no-op integration fixture carries the same compiler, runtime,
target, options, and source-digest identities as the production cache plan;
the implementation's fail-closed identity checks were not weakened.

Still open: current-source sequential pcc1 -> pcc2 -> pcc3 identity and real
action-counter/wall-time/RSS measurements.
