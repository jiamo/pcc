# Self-bootstrap phase reuse — current focused evidence

Date: 2026-08-14

Task: `PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE`

The live implementation partitions only real object-cache misses. In default
auto mode, residual inputs at or above the configured threshold execute in a
serial oversized lane before sub-threshold inputs use the existing bounded
safe lane. Explicit `PCC_SELF_BACKEND_JOBS` remains authoritative; a failure
in the oversized lane prevents both the safe lane and cache publication; an
all-hit plan launches no workers. Existing cache identity, deterministic
result ordering and at-most-four-object worker batches are unchanged.

## Focused gates

Seven exact multi-file scheduler/cache/profile nodes completed fail-fast:

```text
7 passed in 0.12s
```

The task's declared small pass-pipeline gate also completed:

```text
gtimeout 180s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 \
  tests/python/test_py_frontend_ir_pass_pipeline.py \
  -k "cache or profile or deterministic"

4 passed, 81 deselected in 0.29s
```

## Claim boundary

This is current-source host scheduling, cache and profile evidence. The source
identity is not frozen while the separately owned HARNESS work is still
landing compiler changes. The isolated source-current pcc1->pcc2->pcc3 warmup,
six-residual-shard profile, peak RSS, normalized fixed point, exact suites and
five-GC final gate remain open. The row is therefore `DONE_WEAK`, not a
performance completion claim.
